import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.object_history import RunHistory, describe_failure, metrics_of
from modules.quant.backend.object_routes import Services
from modules.quant.backend.work_records import WorkRecords
from tests.unit.quant_runtime_fakes import adapt
from vocabulary.instruments import MarketInstrument, ohlcv_series

INSTRUMENT = MarketInstrument(venueId="binance", symbol="BTC/USDT:USDT", displaySymbol="BTC/USDT:USDT",
              nativeId="BTCUSDT", base="BTC", quote="USDT", settle="USDT", marketType="swap", assetClass="crypto",
              contractOrientation="linear", calendarId="24_7", adjustmentEligible=False).to_dict()
OKX = MarketInstrument(venueId="okx", symbol="BTC/USDT:USDT", displaySymbol="BTC/USDT:USDT",
                       nativeId="BTC-USDT-SWAP", base="BTC", quote="USDT", settle="USDT", marketType="swap", assetClass="crypto",
                       contractOrientation="linear", calendarId="24_7", adjustmentEligible=False).to_dict()
SPY = MarketInstrument(identityVersion=2, instrumentId="vi2_" + "2" * 32, venueId="US", mic="ARCX", symbol="SPY", displaySymbol="SPY",
                       marketType="spot", assetClass="fund", securityType="etf", calendarId="XNYS", currency="USD", adjustmentEligible=True).to_dict()
SERIES = ohlcv_series(MarketInstrument.from_wire(INSTRUMENT), "1h", adjustment="not_applicable").to_dict()
INDEX = MarketInstrument(identityVersion=2, instrumentId="vi2_" + "1" * 32, venueId="US", symbol="^GSPC", displaySymbol="S&P 500",
                         marketType="spot", assetClass="index", securityType="index", calendarId="XNYS", currency="USD").to_dict()
DATA = {"symbols": [{"instrument": INSTRUMENT}], "timeframe": "1h", "since": "2024-01-01", "until": "2024-06-30"}


class FakeContext:
    """The parts of the Host that a run touches; Market's behavior is the scenario."""

    def __init__(self, tmp_path, export):
        self.calls, self.published = [], []
        self.export = export
        self.snapshots = tmp_path / "snapshots"
        self.market_tasks = {}
        self.services = {
            "market.instruments": SimpleNamespace(resolve=self.resolve, get=self.get, terms=self.trading_terms),
            "market.data": SimpleNamespace(coverage=self.coverage, read=self.read_data),
            "market.calendar": SimpleNamespace(bar_times=self.bar_times, sessions=self.sessions),
            "market.research": SimpleNamespace(describe=self.describe),
        }
        self.require = lambda name: self.services[name]
        self.actions = SimpleNamespace(call=self.action_call)
        self.events = SimpleNamespace(publish=lambda name, payload: self.published.append((name, payload)))
        self.track = lambda release: None
        self.platform = SimpleNamespace(
            workspace_activity=SimpleNamespace(current=lambda: "ws"),
            storage=SimpleNamespace(local_config=SimpleNamespace(user_dir=tmp_path / "user")),
            artifacts=SimpleNamespace(snapshot_project=self.snapshot),
            tasks=SimpleNamespace(get=self.get_task, cancel=self.cancel_task))
        adapt(self, tmp_path)

    async def resolve(self, args):
        text = args["text"]
        if text == "^GSPC":
            return {"instrument": INDEX}
        if text in ("BTC/USDT:USDT", "binance:BTC/USDT:USDT"):
            return {"instrument": INSTRUMENT}
        if text == "okx:BTC/USDT:USDT":
            return {"instrument": OKX}
        if text in ("SPY", "US:SPY"):
            return {"instrument": SPY}
        raise ContractError("not_found", f"Market has no instrument {text}.")

    async def get(self, args):
        known = {item["instrumentId"]:item for item in (INSTRUMENT, OKX, SPY, INDEX)}
        items = []
        for value in args.get("records") or args["symbols"]:
            if isinstance(value,dict):
                instrument = value
            elif value in known:
                instrument = known[value]
            else:
                try:
                    instrument = (await self.require("market.instruments").resolve({"text":value}))["instrument"]
                except ContractError as error:
                    if error.code != "not_found":
                        raise
                    items.append({"status":"not_found","symbol":value})
                    continue
            label = instrument["symbol"] if instrument["assetClass"] != "crypto" else instrument["venueId"] + ":" + instrument["symbol"]
            items.append({"instrument":instrument,"symbol":label,"status":"found"})
        return {"items":items,"nextCursor":None}

    async def coverage(self, args):
        from modules.quant.backend.public_settings import timestamp
        row = {"data":args["data"],"source":None} if args["data"] != "bars" else {
            "capability":"ohlcv","timeframe":"1h","source":{"sourceId":"fixture","name":"Fixture"},"local":None,
            "status":"not_stored","problem":None,"revision":0,"publishDelay":None,"publishedThrough":timestamp(time.time()*1000),
            "available":{"periods":["1m","1d"],"earliest":None,"live":"stream"}}
        return {"columns":[{"name":k,"unit":None} for k in row],"rows":[list(row.values())],"nextCursor":None}

    async def read_data(self, args):
        return {"columns": {"time": [], "closeTime": [], "open": [], "high": [], "low": [], "close": [], "volume": []},
                "hasMoreBefore": False, "nextBefore": None, "revision": 1, "source": {"sourceId": "fixture", "name": "Fixture"},
                "gaps": [], "fill": {"state": "not_needed", "problem": None}}

    async def sessions(self, args):
        return {"calendarId": args["calendarId"], "timezone": "UTC", "mode": "continuous" if args["calendarId"] == "24_7" else "exchange",
                "evidence": "verified", "coveredFrom": None, "coveredUntil": None, "sessions": []}

    async def bar_times(self, args):
        step = int(args["timeframe"][:-1]) * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[args["timeframe"][-1]] * 1_000_000
        start, end = int(args["start"]), int(args["end"])
        opens = list(range((start + step - 1) // step * step, end, step))
        if args.get("after") is not None:
            opens = [at for at in opens if at > int(args["after"])]
        selected = opens[:args.get("limit", 5000)]
        return {"evidence": "verified", "reason": None, "timezone": "UTC", "open": [str(at) for at in selected],
                "close": [str(at + step) for at in selected], "next": str(selected[-1]) if len(selected) < len(opens) else None}

    async def describe(self, args):
        return {"kinds": [], "indices": [{"index": INDEX, "source": {"sourceId": "fixture", "name": "Fixture"}, "start": None, "end": None,
                            "pointInTime": True, "survivorshipBias": False, "note": None}]}

    async def trading_terms(self, args):
        return {"items": [{"instrumentId": identity, "fees": {"maker": .0002, "taker": .0005, "basis": "notional"},
                          "minQuantity": .001, "quantityIncrement": .001, "quantityUnit": "base", "contractMultiplier": 1,
                          "priceIncrement": .1, "unavailable": {}} for identity in (args.get("symbols") or [s["instrumentId"] for s in args["series"]])]}

    async def get_task(self, args):
        work = self.market_tasks[args["taskId"]]
        await asyncio.sleep(0)
        state, result, error = "running", None, None
        if work.cancelled():
            state = "cancelled"
        elif work.done():
            failure = work.exception()
            if failure is None:
                state, result = "completed", work.result()
            elif isinstance(failure, ContractError):
                state, error = "failed", {"code": failure.code, "message": failure.message, "details": failure.details}
            else:
                raise failure
        return {"state": state, "result": result, "error": error, "progress": None}

    async def cancel_task(self, args):
        work = self.market_tasks[args["taskId"]]
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        return {"cancelled": True}

    async def action_call(self, name, args):
        self.calls.append(name)
        if name == "developer.projects.open":
            return {"projectId": "p", "contextId": "c"}
        if name == "developer.documents.list":
            return {"documents": [], "nextCursor": None}
        if name == "developer.documents.open":
            return {"documentId": args["source"]["uri"], "version": 1, "dirty": False, "conflict": False}
        if name == "developer.documents.edit":
            self.edited = args["content"]
            return {"documentId":args["documentId"], "version":args["expectedVersion"] + 1}
        if name == "developer.documents.save":
            from modules.quant.backend.project_paths import local_path
            local_path(args["documentId"]).write_text(self.edited, encoding="utf-8")
            return {"status":"saved", "version": args["expectedVersion"]}
        if name in {"market.fetch", "market.export"}:
            async def perform():
                if name == "market.fetch":
                    return {"state": "completed", "failures": [], "failureCount": 0}
                return await self.export(args)
            identity = f"task-{len(self.market_tasks)}"
            self.market_tasks[identity] = asyncio.create_task(perform())
            return {"taskId": identity}
        raise AssertionError(name)

    async def snapshot(self, args):
        target = self.snapshots / hashlib.sha256(args["projectPath"].encode()).hexdigest()[:12]
        shutil.copytree(args["projectPath"], target)
        files = [{"path": path.relative_to(target).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(target.rglob("*")) if path.is_file()]
        return {"files": files, "snapshotPath": str(target), "digest": hashlib.sha256(repr(files).encode()).hexdigest()}


def setup(tmp_path, export):
    ctx = FakeContext(tmp_path, export)
    records = WorkRecords(tmp_path / "records")
    shell = Services(ctx, records)
    return ctx, records, shell.store, shell.runs


# The moving-average starter these tests were written around (a new strategy now starts as an empty skeleton).
STARTER = '"""Moving-average crossover: long when the fast average is above the slow one, flat otherwise.\n\nWrite only the trading logic. The data, venue, fees and account come from the strategy\'s data settings;\nthe numbers declared with q.param show up as editable fields next to the chart.\n"""\nfrom collections import deque\n\nimport talyx_quant as q\nfrom nautilus_trader.model import OrderSide\n\n\nclass MovingAverageCrossover(q.Strategy):\n    def __init__(self):\n        super().__init__()\n        self.fast = q.param("fast", 10, min=2, max=200, label="Fast window")\n        self.slow = q.param("slow", 30, min=3, max=500, label="Slow window")\n        self.stake = q.param("stake", 1000.0, min=1, label="Position size (quote currency)")\n        if self.fast >= self.slow:\n            raise ValueError("The fast window must be shorter than the slow window")\n        self.closes = deque(maxlen=self.slow)\n\n    def on_start(self):\n        self.instrument = self.cache.instrument(self.instrument_id)\n        self.subscribe_bars(self.bar_type)\n\n    def on_bar(self, bar):\n        self.closes.append(float(bar.close))\n        if len(self.closes) < self.slow:\n            return\n        closes = list(self.closes)\n        fast = sum(closes[-self.fast:]) / self.fast\n        slow = sum(closes) / self.slow\n        flat = not self.cache.positions_open(instrument_id=self.instrument_id)\n        if fast > slow and flat:\n            quantity = self.instrument.make_qty(self.stake / float(bar.close))\n            if quantity.as_double() > 0:\n                self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, quantity))\n        elif fast < slow and not flat:\n            self.close_all_positions(self.instrument_id)\n\n    def on_stop(self):\n        self.cancel_all_orders(self.instrument_id)\n        self.close_all_positions(self.instrument_id)\n'


async def new_strategy(store, data=DATA):
    created = await store.create({"kind": "strategy", "name": "Trend"})
    store.source_path(store.read(created["id"])).write_text(STARTER, encoding="utf-8")
    await store.update_settings(created["id"], {"data": data})
    return created["id"]


async def settled(runs, identity):
    while identity in runs.tasks:
        await asyncio.sleep(.01)
    return runs.read(identity)


@pytest.mark.parametrize('failure', [TypeError('invalid price currency'), ValueError('invalid backend material')])
def test_unexpected_preparation_error_finishes_the_run_instead_of_leaving_preparing(tmp_path, monkeypatch, failure):
    async def export(args):
        raise AssertionError('Data capture should not start')
    async def scenario():
        _, records, store, runs = setup(tmp_path, export)
        async def prepare(*args):
            raise failure
        monkeypatch.setattr(runs, 'prepare_data', prepare)
        obj = await new_strategy(store)
        record = await runs.start(obj, {})
        run = await settled(runs, record['id'])
        assert run['state'] == 'failed' and run['endedAt'] is not None
        assert run['error']['stage'] == 'data' and run['error']['code'] == 'internal_error'
        assert type(failure).__name__ in run['message'] and str(failure) in run['message']
        records.close()
    asyncio.run(scenario())


def test_source_limit_or_refusal_is_the_runs_failure_reason_and_is_not_retried(tmp_path):
    reason = {"source": "binance", "reason": "HTTP 418: banned until 2026-10-30T12:00:00Z"}
    attempts = []

    async def export(args):
        attempts.append(args["series"])
        raise ContractError("rate_limited", "Binance refused the request: too many requests", reason)

    async def scenario():
        ctx, records, store, runs = setup(tmp_path, export)
        identity = (await runs.start(await new_strategy(store))) ["id"]
        run = await settled(runs, identity)
        assert run["state"] == "failed" and run["stage"] == "data"
        assert run["message"] == "Binance refused the request: too many requests"
        assert run["error"]["code"] == "rate_limited" and run["error"]["details"] == reason
        assert len(attempts) == 1
        assert ("object_run_changed", {"runId": identity, "objectId": run["objectId"], "workspaceId": "ws", "state": "failed"}) in ctx.published
        listed = RunHistory(records, runs).list(run["objectId"], {})
        assert [item["state"] for item in listed["items"]] == ["failed"]
        assert listed["items"][0]["error"]["details"] == reason
    asyncio.run(scenario())


def test_an_error_in_the_authors_code_is_reported_at_the_authors_file_and_line(tmp_path):
    def frame(root, file, line, function, text, path=None):
        return {"root": root, "file": file, "path": path or "C:/run/" + file, "line": line, "function": function, "text": text}
    result = {"failure": {"type": "ZeroDivisionError", "message": "division by zero", "traceback": "Traceback ...",
                          "frames": [frame("talyx", "quant/native/object_backtest.py", 20, "run", "run_one()"), frame("source", "strategy.py", 27, "on_bar", "ratio()"),
                                     frame("source", "helpers.py", 3, "ratio", "1 / 0"), frame("package", "nautilus_trader/trading/strategy.py", 9, "inner", "x")]}}
    failure = describe_failure(result, str(tmp_path / "object"), "", 1)
    # The top-level place is the deepest frame in the author's own code, even when package code is deeper still.
    assert (failure["file"], failure["line"], failure["type"]) == ("helpers.py", 3, "ZeroDivisionError")
    assert failure["message"] == "ZeroDivisionError: division by zero"
    assert [(item["file"], item["path"]) for item in failure["frames"]] == [
        ("quant/native/object_backtest.py", "C:/run/quant/native/object_backtest.py"),
        ("strategy.py", (tmp_path / "object" / "strategy.py").as_posix()), ("helpers.py", (tmp_path / "object" / "helpers.py").as_posix()),
        ("nautilus_trader/trading/strategy.py", "C:/run/nautilus_trader/trading/strategy.py")]
    crashed = describe_failure(None, str(tmp_path), "Segmentation fault\n", -11)
    assert crashed["code"] == "process_failed" and "exited with code -11" in crashed["message"] and "Segmentation fault" in crashed["message"]


def test_metrics_are_the_headline_numbers_of_the_native_report():
    unit = {"nativeStatistics": {"total_orders": 112, "total_positions": 56, "stats_pnls": {"USDT": {"Win Rate": .27}},
            "accountPerformance": [{"account": "BINANCE-001", "currency": "USDT", "totalReturn": -.001, "maximumDrawdown": -.002, "dailySharpe": -1.1,
                                    "initialEquityNumeric": 100000., "finalEquityNumeric": 99906.7, "equityChangeNumeric": -93.3}]}}
    assert metrics_of({"metrics": {}, "units": [{"id": "x"}, unit]}) == {"orders": 112, "positions": 56, "totalReturn": -.001, "maximumDrawdown": -.002, "dailySharpe": -1.1,
                                                          "initialEquity": 100000., "finalEquity": 99906.7, "equityChange": -93.3, "winRate": .27}


def test_cancel_marks_the_run_cancelled_and_a_restart_marks_unfinished_runs_interrupted(tmp_path):
    started = asyncio.Event()

    async def export(args):
        started.set()
        await asyncio.Event().wait()

    async def scenario():
        ctx, records, store, runs = setup(tmp_path, export)
        object_id = await new_strategy(store)
        cancelled = (await runs.start(object_id))["id"]
        await asyncio.wait_for(started.wait(), 5)
        assert (await runs.cancel(cancelled))["state"] == "cancelled"
        assert runs.read(cancelled)["endedAt"] is not None
        running = records.put("qrun", {**runs.read(cancelled), "id": "run-unfinished", "state": "running"})
        records.close()
        reopened = WorkRecords(tmp_path / "records")
        assert reopened.get("qrun", running["id"])["state"] == "interrupted"
        assert reopened.get("qrun", cancelled)["state"] == "cancelled"
        reopened.close()
    asyncio.run(scenario())


def test_runs_compare_by_parameters_code_and_metrics_and_carry_notes(tmp_path):
    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        object_id = await new_strategy(store)
        def finished(identity, created, params, digest, orders):
            return records.put("qrun", {"id": identity, "objectId": object_id, "objectKind": "strategy", "runKind": "run", "mode": "run", "publish": None, "name": "Trend", "workspaceId": "ws", "state": "completed",
                "stage": "backtest", "createdAt": created, "params": params, "overrides": {}, "settings": store.read(object_id)["settings"], "message": None, "error": None,
                "code": {"digest": digest}, "data": None, "environment": None, "recipe": None, "metrics": {"orders": orders, "totalReturn": orders / 1000},
                "starred": False, "note": "", "exitCode": 0, "endedAt": created + 1})
        finished("run-a", 1, {"fast": 10, "slow": 30}, "one", 100)
        finished("run-b", 2, {"fast": 8, "slow": 30}, "two", 150)
        history = RunHistory(records, runs)
        assert history.annotate("run-b", {"starred": True, "note": "tighter fast window"})["starred"] is True
        newest = history.list(object_id, {})["items"]
        assert [item["id"] for item in newest] == ["run-b", "run-a"]
        assert newest[0]["parameterDiff"] == {"fast": {"before": 10, "after": 8}} and newest[0]["note"] == "tighter fast window"
        assert [item["id"] for item in history.list(object_id, {"starredOnly": True})["items"]] == ["run-b"]
        comparison = history.compare("run-b", "run-a")
        assert comparison["parameterDiff"] == {"fast": {"before": 10, "after": 8}} and comparison["codeChanged"] and not comparison["dataChanged"]
        assert comparison["metrics"]["orders"] == {"current": 150, "previous": 100, "difference": 50}
    asyncio.run(scenario())


def test_objects_list_by_kind_workspace_and_folder_and_reject_bad_settings(tmp_path):
    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        first = await new_strategy(store)
        other = (await store.create({"kind": "strategy", "name": "Other", "folder": "Ideas"}))["id"]
        store.create_folder({"kind": "strategy", "name": "Empty"})
        records.put("qobject", {**records.find("qobject", other), "workspaceId": "elsewhere"})
        listed = store.list({"kind": "strategy"})
        assert [item["id"] for item in listed["items"]] == [first] and listed["items"][0]["lastRun"] is None
        assert {item["kind"]: item["count"] for item in listed["kinds"]}["strategy"] == 1 and {item["kind"] for item in listed["kinds"] if item["creatable"]} == {"notebook", "strategy", "factor", "model", "allocation"}
        assert {item["id"] for item in store.list({"all": True})["items"]} == {first, other}
        assert {(item["name"], item["count"]) for item in store.list({"all": True})["folders"]} == {("Ideas", 1), ("Empty", 0)}
        assert [item["id"] for item in store.list({"all": True, "folder": "Ideas"})["items"]] == [other]
        with pytest.raises(ContractError, match="cannot be created yet"):
            await store.create({"kind": "library"})
        for data, message in (({"timeframe": "7m"}, "Choose a bar period"), ({"since": "2024-02-01", "until": "2024-01-01"}, "not be before"),
                              ({"since": "01/02/2024"}, "YYYY-MM-DD"), ({"symbols": [{"instrument": {}}]}, "copied from a Market search")):
            with pytest.raises(ContractError, match=message):
                await store.update_settings(first, {"data": data})
        with pytest.raises(ContractError, match="between 2 and 200"):
            await store.update_settings(first, {"params": {"fast": 1}})
        with pytest.raises(ContractError, match="Read the current object"):
            await store.update_settings(first, {"params": {"fast": 5}, "expectedVersion": 1})
        detail = await store.update_settings(first, {"params": {"fast": 5}})
        assert detail["settings"]["params"] == {"fast": 5} and [item["name"] for item in detail["parameters"]] == ["fast", "slow", "stake"]
        assert store.effective_parameters(store.read(first), Path(detail["directory"], "strategy.py").read_text(encoding="utf-8")) == {"fast": 5, "slow": 30, "stake": 1000.0}
    asyncio.run(scenario())


def test_use_this_version_writes_the_runs_code_through_developer_and_restores_its_parameters(tmp_path):
    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        object_id = await new_strategy(store)
        obj = store.read(object_id)
        path = store.source_path(obj)
        original = path.read_text(encoding="utf-8")
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "strategy.py").write_text(original, encoding="utf-8")
        files = [{"path": "strategy.py", "sha256": hashlib.sha256((snapshot / "strategy.py").read_bytes()).hexdigest()}]
        path.write_text(original.replace('"fast", 10', '"fast", 7'), encoding="utf-8")
        store.records.put("qobject", {**store.read(object_id), "settings": {"data": DATA, "params": {"fast": 7}, "account": {}}})
        editor = {"version": 3, "content": None, "saved": []}

        async def call(name, args):
            if name == "developer.projects.open":
                return {"projectId": "project"}
            if name == "developer.documents.open":
                return {"documentId": "d", "version": editor["version"], "dirty": False, "conflict": False}
            if name == "developer.documents.edit":
                assert args["expectedVersion"] == editor["version"]
                editor.update(version=editor["version"] + 1, content=args["content"])
                return {"documentId": "d", "version": editor["version"]}
            if name == "developer.documents.save":
                assert args["expectedVersion"] == editor["version"]
                editor["saved"].append(path.read_text(encoding="utf-8"))
                path.write_bytes(editor["content"].encode("utf-8"))
                return {"status": "saved", "message": None}
            raise AssertionError(name)

        ctx.actions = SimpleNamespace(call=call)
        run = records.put("qrun", {"id": "run-old", "runKind": "run", "mode": "run", "publish": None, "objectId": object_id, "state": "completed", "stage": "backtest", "createdAt": 1, "workspaceId": "ws", "name": "Trend",
                                   "params": {"fast": 10, "slow": 30, "stake": 1000.0}, "settings": {"data": DATA}, "message": None, "error": None, "metrics": {},
                                   "starred": False, "note": "", "endedAt": 2, "code": {"entry": "strategy.py", "digest": "d", "files": files, "snapshotPath": str(snapshot), "directory": obj["directory"]}})
        detail = await runs.apply(run["id"])
        assert path.read_text(encoding="utf-8") == original and editor["saved"] == [original.replace('"fast", 10', '"fast", 7')]
        assert detail["settings"]["params"] == {"fast": 10, "slow": 30, "stake": 1000.0}
        (snapshot / "strategy.py").write_text("changed", encoding="utf-8")
        with pytest.raises(ContractError, match="changed. It cannot be restored"):
            await runs.apply(run["id"])
    asyncio.run(scenario())


def test_lists_show_each_objects_latest_run_and_terminal_runs_emit_the_research_completed_fact(tmp_path):
    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        object_id = await new_strategy(store)
        def finished(identity, created, state, workspace="ws"):
            return records.put("qrun", {"id": identity, "runKind": "run", "mode": "run", "publish": None, "objectId": object_id, "name": "Trend", "state": state, "stage": "backtest", "createdAt": created, "workspaceId": workspace,
                "params": {}, "settings": store.read(object_id)["settings"], "message": None, "error": None, "code": None, "metrics": {}, "starred": False, "note": "", "endedAt": created})
        finished("run-1", 1, "failed")
        finished("run-2", 2, "completed")
        item = store.list({})["items"][0]
        assert (item["lastRun"]["id"], item["lastRun"]["state"]) == ("run-2", "completed") and store.detail(store.read(object_id))["lastRun"]["id"] == "run-2"
        facts = records.db.execute("SELECT value FROM quant_trigger_facts WHERE source='research' ORDER BY seq").fetchall()
        payloads = [json.loads(row[0])["payload"] for row in facts]
        assert payloads == [{"runId": "run-1", "state": "failed", "objectId": object_id},
                            {"runId": "run-2", "state": "completed", "objectId": object_id}]
        records.put("qrun", {**records.find("qrun", "run-2"), "note": "again"})
        assert len(records.db.execute("SELECT 1 FROM quant_trigger_facts").fetchall()) == 2
    asyncio.run(scenario())


def test_only_an_empty_folder_can_be_deleted(tmp_path):
    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        kept = (await store.create({"kind": "strategy", "name": "Kept", "folder": "Ideas"}))["id"]
        store.create_folder({"kind": "strategy", "name": "Spare"})
        with pytest.raises(ContractError, match="still holds 1 object") as error:
            store.delete_folder({"kind": "strategy", "name": "Ideas"})
        assert error.value.code == "conflict" and error.value.details["objects"] == ["Kept"]
        assert store.delete_folder({"kind": "strategy", "name": "Spare"})["deleted"] is True
        assert {item["name"] for item in store.list({})["folders"]} == {"Ideas"}
        with pytest.raises(ContractError, match="no folder Spare"):
            store.delete_folder({"kind": "strategy", "name": "Spare"})
        store.update(kept, {"folder": None})
        store.delete_folder({"kind": "strategy", "name": "Ideas"})
        assert store.list({})["folders"] == []
    asyncio.run(scenario())


def test_the_table_route_takes_the_old_filter_and_observation_and_instruments_come_out_in_the_shared_shape():
    from modules.quant.backend.object_data import shared_instrument
    from modules.quant.backend.object_routes import TableQuery
    body = TableQuery.model_validate({"name": "t.parquet", "observation": "2024-01-01", "filter": {"column": "instrument_id", "equals": "BTCUSDT.OKX"}, "limit": 5,
                                      "filters": [{"column": "a", "operator": "in", "value": [1, 2]}], "sort": {"column": "ts", "descending": True}})
    assert body.args()["filter"] == {"column": "instrument_id", "equals": "BTCUSDT.OKX"}
    from vocabulary.instruments import MarketInstrument
    market = MarketInstrument(venueId="okx", symbol="BTC/USDT:USDT", displaySymbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT", marketType="swap",
                              assetClass="crypto", contractOrientation="linear", contractSize=0.01, nativeId="BTC-USDT-SWAP", adapterId="okx", name="x")
    shared = shared_instrument(market.to_dict())
    assert shared["instrumentId"] == market.instrumentId and shared["contractSize"] == "0.01" and "sourceId" not in shared and shared["nativeId"] == "BTC-USDT-SWAP"


def pipeline(runs, monkeypatch, tmp_path):
    """Everything around the environment step is cheap; the run still goes through `execute`."""
    source = tmp_path / "captured"
    source.mkdir()

    async def capture(obj, directory):
        directory.joinpath("source").mkdir()
        return {"digest": "d" * 64, "files": [], "snapshotPath": str(source)}

    async def prepare_data(identity, obj, directory):
        return {"summary": {"traded": 1, "rows": 1000}}

    async def launch(identity, obj, directory, prepared, state):
        return {"leaseId": "lease"}

    async def finish(identity, process, directory, research):
        runs.update(identity, exitCode=0)

    monkeypatch.setattr(runs, "capture_code", capture)
    monkeypatch.setattr(runs, "prepare_data", prepare_data)
    monkeypatch.setattr(runs, "launch", launch)
    monkeypatch.setattr(runs, "finish", finish)
    monkeypatch.setattr(runs, "ending", lambda identity, directory: {"state": "completed", "message": None, "metrics": {}})
    monkeypatch.setattr("modules.quant.backend.object_runs.write_recipe", lambda *args: {"pythonVersion": "3.13.13", "lockPath": "x", "lockDigest": "y", "engine": "strategy"})
    monkeypatch.setattr("modules.quant.backend.object_runs.recipe_lock", lambda recipe: "lock")


def environment_host(ctx, *, outcomes, log=None):
    """Platform environment preparation as talyx_sdk.uv answers it: a new submission is queued, and `outcomes` are the states it then passes
    through, in order; every record names its log (`stderrPath`)."""
    seen = {"submitted": [], "retried": 0}
    states = iter(outcomes)
    stderr = str(log) if log is not None else "e"

    async def submit(args):
        seen["submitted"].append(args)
        return {"preparationId": "prep", "state": "queued", "stderrPath": stderr}

    async def get(args):
        return {"stderrPath": stderr, **next(states)}

    async def retry(args):
        seen["retried"] += 1
        return {"preparationId": "prep", "state": "queued", "stderrPath": stderr}

    ctx.platform.execution = SimpleNamespace()
    ctx.platform.leases = SimpleNamespace(close=lambda args: asyncio.sleep(0, {}))
    ctx.platform.python_env = SimpleNamespace(submit=submit, get=get, retry=retry, acquire=lambda args: asyncio.sleep(0, {}))
    return seen


READY = {"preparationId": "prep", "state": "completed", "interpreterPath": "python.exe", "environmentPath": "env", "pythonVersion": "3.13.13",
         "stdoutPath": "o", "stderrPath": "e"}


def test_a_run_whose_environment_cannot_be_prepared_fails_with_the_last_line_of_its_log(tmp_path, monkeypatch):
    async def export(args):
        raise AssertionError("not reached")

    async def scenario():
        ctx, records, store, runs = setup(tmp_path, export)
        pipeline(runs, monkeypatch, tmp_path)
        log = tmp_path / "prep.stderr.log"
        log.write_text("Resolving\nerror: No solution found when resolving dependencies\n", encoding="utf-8")
        # It fails, and the run asks the platform to try once more before it gives up.
        seen = environment_host(ctx, outcomes=[{"preparationId": "prep", "state": "running"}, {"preparationId": "prep", "state": "failed"}, {"preparationId": "prep", "state": "failed"}], log=log)
        identity = (await runs.start(await new_strategy(store)))["id"]
        run = await settled(runs, identity)
        assert run["state"] == "failed" and run["message"].endswith("could not be prepared: error: No solution found when resolving dependencies") and seen["retried"] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('index', [None, {'selection': {'instrument': INDEX}, 'intervals': []}])
def test_the_worker_launch_reads_its_invocation_and_logs_into_the_output_folder(tmp_path, index):
    async def export(args):
        raise AssertionError("not reached")

    async def scenario():
        ctx, records, store, runs = setup(tmp_path, export)
        launched = []

        async def start(launch):
            launched.append(launch)
            return {"leaseId": "lease"}

        ctx.platform.execution = SimpleNamespace(start=start)
        directory = runs.directory("run-x")
        directory.mkdir(parents=True)
        bars, prices = directory / "bars.parquet", directory / "prices.parquet"
        values = directory / "library-values.parquet"
        bars.write_bytes(b"b")
        prices.write_bytes(b"p")
        values.write_bytes(b"v")
        runs.data_progress("run-x")("prices", 3, 3, [])  # the data stage reported its progress (it once wrote into output/: every run failed, FileExistsError)
        records.put("qrun", {"id": "run-x", "createdAt": int(time.time() * 1000), "objectId": "o", "workspaceId": "ws", "state": "running", "params": {}, "common": None, "code": {"digest": "d"}, 'data': {'library': [], 'models': []}})
        obj = {"name": "Trend", "entry": "strategy.py", 'preset': None, 'settings': {'data': {}}}
        prepared = {"instruments": [], "venues": [], "annualization": 365, "barTypes": [], "barsPath": str(bars), "pricesPath": str(prices), "libraryPath": str(values), 'index': index}
        await runs.launch("run-x", obj, directory, prepared, {"interpreterPath": "python.exe"})
        launch, = launched
        assert json.loads((directory / 'invocation.json').read_text(encoding='utf-8'))['index'] == index
        assert launch["argv"][0] == "python.exe" and launch["argv"][-1] == str(directory / "invocation.json") and launch["cwd"] == str(directory / "source")
        assert Path(launch["stdout_path"]).parent == Path(launch["stderr_path"]).parent == directory / "output"
        assert "lines" not in launch, "with no models bound the backtest has no line protocol"
    asyncio.run(scenario())

def test_a_strategy_made_from_a_preset_is_the_users_own_copy(tmp_path):
    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        created = await store.create({"kind": "strategy", "preset": "ranked"})
        # The preset's code is copied into the new strategy, which is opened in Developer and is free to edit.
        assert created["name"] == "Top N by signal" and created["preset"] is None and created["developer"] == {"projectId": "p", "contextId": "c"}
        assert [(item["name"], item["type"]) for item in created["parameters"][:2]] == [("signal", "library"), ("signal_field", "text")]
        record = store.read(created["id"])
        assert "class RankedPortfolio" in store.source_path(record).read_text(encoding="utf-8")
        store.source_path(record).write_text("# mine\n", encoding="utf-8")
        assert store.code(record) == "# mine\n"
        await store.update_settings(created["id"], {"data": DATA})
        mine = await store.copy(created["id"])
        assert mine["name"] == "Top N by signal (copy)" and mine["preset"] is None
        with pytest.raises(ContractError, match="A ready-made strategy is one of"):
            await store.create({"kind": "strategy", "preset": "nonsense"})
        with pytest.raises(ContractError, match="A ready-made strategy is one of"):
            await store.create({"kind": "factor", "preset": "ranked"})
        notebook = await store.create({"kind": "notebook"})
        with pytest.raises(ContractError, match="Only a strategy can be copied"):
            await store.copy(notebook["id"])
    asyncio.run(scenario())


def test_shorting_with_a_ready_made_strategy_needs_perpetuals(tmp_path):
    spot = {**INSTRUMENT, "symbol": "BTC/USDT", "displaySymbol": "BTC/USDT", "marketType": "spot", "contractOrientation": "none"}

    async def scenario():
        ctx, records, store, runs = setup(tmp_path, None)
        created = (await store.create({"kind": "strategy", "preset": "ranked"}))["id"]
        record = store.read(created)
        store.check_short(record, {"short_n": 0}, {"symbols": [{"instrument": spot}]})
        store.check_short(record, {"short_n": 2}, {"symbols": [{"instrument": INSTRUMENT}]})
        with pytest.raises(ContractError, match=r"BTC/USDT cannot be shorted: shorting needs perpetuals or stocks with shorting allowed. Set short_n to 0") as error:
            store.check_short(record, {"short_n": 2}, {"symbols": [{"instrument": INSTRUMENT}, {"instrument": spot}]})
        assert error.value.code == "invalid_input" and error.value.details["symbols"] == ["BTC/USDT"]
        await store.update_settings(created, {"data": {**DATA, "symbols": [{"instrument": spot}]}})
        with pytest.raises(ContractError, match="cannot be shorted"):
            await runs.start(created, {"params": {"short_n": 1}})
        own = (await store.create({"kind": "strategy"}))["id"]
        store.check_short(store.read(own), {"short_n": 1}, {"symbols": [{"instrument": spot}]})
    asyncio.run(scenario())


@pytest.mark.parametrize("writes", [True, False])
def test_stopping_a_backtest_answers_at_once_and_keeps_the_part_it_writes(tmp_path, monkeypatch, writes):
    """Stop waited up to 15 seconds for the backtest and then ended its process, losing what it had (an S&P run: 33 seconds, then
    nothing). The answer is now the run stopping; the backtest is asked to stop, and the run ends with the part it writes. Only a backtest
    that writes nothing within the allowed time has its process ended."""
    from modules.quant.backend import object_runs as O

    async def export(args):
        raise AssertionError("not reached")

    async def scenario():
        ctx, records, store, runs = setup(tmp_path, export)
        pipeline(runs, monkeypatch, tmp_path)
        environment_host(ctx, outcomes=[READY])
        ended = []
        ctx.platform.execution.cancel = lambda args: asyncio.sleep(0, ended.append(args))
        monkeypatch.setattr(O, "STOP_SECONDS", .3)

        async def launch(identity, obj, directory, prepared, state):
            runs.processes[identity] = "lease"
            return "lease"

        async def finish(identity, process, directory, research):
            while not (directory / "output" / "stop.request").exists() or not writes:
                await asyncio.sleep(.01)
            runs.update(identity, exitCode=0)
        monkeypatch.setattr(runs, "launch", launch)
        monkeypatch.setattr(runs, "finish", finish)
        monkeypatch.setattr(runs, "ending", lambda identity, directory: {"state": "cancelled", "message": "Stopped by the user", "metrics": {}})
        identity = (await runs.start(await new_strategy(store)))["id"]
        async with asyncio.timeout(10):
            while runs.read(identity)["stage"] != "backtest" or identity not in runs.processes:
                await asyncio.sleep(.01)
        began = time.monotonic()
        answer = await runs.cancel(identity)
        assert answer["state"] == "cancel_requested" and time.monotonic() - began < .2
        done = await settled(runs, identity)
        assert done["state"] == "cancelled"
        assert (done["message"] == "Stopped by the user" and ended == []) if writes else ended == [{"lease": "lease"}]
    asyncio.run(scenario())


def test_click_and_source_validation_freeze_the_whole_project_and_settings(tmp_path, monkeypatch):
    async def scenario():
        _, records, store, runs = setup(tmp_path, None)
        object_id = await new_strategy(store)
        root = Path(store.read(object_id)["directory"])
        (root / "helper.py").write_text("def signal(): return 1")
        (runs.common.root / "shared.py").write_text("x = 1")
        await store.update_settings(object_id, {"run": {"capital": 100000, "costs": {"commissionBps": 5}, "heldOutFrom": "2024-04-01"},
                                               "params": {"fast": 20}})
        async def queued(*_):
            await asyncio.sleep(0)
        monkeypatch.setattr(runs, "execute", queued)
        first = await runs.start(object_id)
        (root / "helper.py").write_text("def signal(): return 2")
        (runs.common.root / "shared.py").write_text("x = 2")
        assert (runs.directory(first["id"]) / "source" / "helper.py").read_text() == "def signal(): return 1"
        source = records.put("qrun", {**first, "state": "completed", "data": {}, "settings": {
            **first["settings"], "data": {**first["settings"]["data"], "until": "2024-06-30", "untilLatest": True}}})
        await store.update_settings(object_id, {"run": {"capital": 200000, "costs": {"commissionBps": 50}, "heldOutFrom": None}, "params": {"fast": 99}})
        held = await runs.start(object_id, {"kind": "heldout", "of": source["id"]})
        assert held["of"] == first["id"] and held["params"]["fast"] == 20
        assert held["settings"]["run"] == {"capital": 100000, "costs": {"commissionBps": 5}}
        assert held["settings"]["data"] == source["settings"]["data"]
        assert held["code"] == first["code"] and held["common"] == first["common"]
        assert (runs.directory(held["id"]) / "common" / "shared.py").read_text() == "x = 1"
        from modules.quant.backend.object_sweeps import Sweeps
        sweeps = Sweeps(runs)
        monkeypatch.setattr(sweeps, "execute", queued)
        request = {"axes": [{"name": "@costMultiple", "values": [0, 1, 2]}], "of": source["id"]}
        sweep = await sweeps.start(object_id, request)
        assert sweep["code"] == source["code"] and sweep["settings"]["data"] == source["settings"]["data"]
        cells = sweep["sweep"]["cases"]
        assert sweeps.reusable(sweep, cells[0], {}) is None and sweeps.reusable(sweep, cells[2], {}) is None
        runs.update(source["id"], metrics={"cagr": .1})
        assert sweeps.reusable(sweep, cells[1], {})[0] == source["id"]
        await asyncio.gather(*runs.tasks.values())
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["strategy", "allocation"])
def test_use_version_restores_the_project_and_lists_unrestored_files(kind, tmp_path, monkeypatch):
    from modules.quant.backend.project_paths import local_path

    async def scenario():
        ctx = FakeContext(tmp_path, None)
        records = WorkRecords(tmp_path / "records")
        shell = Services(ctx, records)
        store = shell.store
        if kind == "strategy":
            identity = await new_strategy(store)
            api = shell.runs
            async def queued(*_):
                pass
            monkeypatch.setattr(api, "execute", queued)
        else:
            identity = (await store.create({"kind": "allocation"}))["id"]
            await store.update_settings(identity, {"data": DATA, "params": {"objective": "equal_weight"}})
            api = shell.allocation
            await api.write_as_code(identity)
            monkeypatch.setattr(api, "_launch", lambda _: None)
            monkeypatch.setattr(api.data, "calendar_items", lambda _: asyncio.sleep(0, []))
        obj = store.read(identity)
        root = Path(obj["directory"])
        entry = root / obj["entry"]
        before = entry.read_bytes()
        (root / "helper.py").write_text("def signal(): return 1")
        (root / "removed.py").write_text("day = 1")
        (root / "weights.bin").write_bytes(bytes([255]))
        (shell.common.root / "pool.py").write_text("pool = []")
        run = await api.start(identity)
        rec = api.read(run["id"])
        entry.write_bytes(before + b"# changed")
        (root / "helper.py").write_text("def signal(): return 9")
        (root / "extra.py").write_text("extra = True")
        (root / "removed.py").unlink()
        (root / "weights.bin").write_bytes(bytes([254]))
        (shell.common.root / "pool.py").write_text("pool = [1]")
        store.save(obj, settings={**obj["settings"], "data": {**obj["settings"]["data"], "since": "2020-01-01"},
                                  "params": {"fast": 99} if kind == "strategy" else {"objective": "min_risk"}})
        original_call = ctx.actions.call
        drafts, history = {}, {}
        async def documents(name, args):
            if name == "developer.documents.create":
                (root / args["relativePath"]).write_bytes(args["content"].encode())
                return {}
            if name == "developer.documents.edit":
                drafts[args["documentId"]] = args["content"]
                return {"documentId": args["documentId"], "version": 2}
            if name == "developer.documents.save":
                path = local_path(args["documentId"])
                history[path.name] = path.read_bytes()
                path.write_bytes(drafts[args["documentId"]].encode())
                return {"status": "saved"}
            return await original_call(name, args)
        ctx.actions = SimpleNamespace(call=documents)
        result = await api.apply(run["id"])
        assert result["restore"] == {"extraFiles": ["extra.py"], "nonTextFiles": ["weights.bin"], "commonDifferences": ["pool.py"]}
        assert result["settings"]["data"] == rec["settings"]["data"]
        assert result["settings"]["params"] == (rec["params"] if kind == "strategy" else rec["settings"]["params"])
        assert entry.read_bytes() == before and history[entry.name] == before + b"# changed"
        assert (root / "helper.py").read_text() == "def signal(): return 1" and (root / "removed.py").read_text() == "day = 1"
        assert (root / "weights.bin").read_bytes() == bytes([254]) and (root / "extra.py").is_file()
        async def unavailable(*_):
            return None
        monkeypatch.setattr(api.ctx, "developer", unavailable)
        with pytest.raises(ContractError) as missing:
            await api.apply(run["id"])
        assert missing.value.code == "unavailable"
    asyncio.run(scenario())


def test_stopping_during_environment_preparation_ignores_queued_allocations(tmp_path):
    async def scenario():
        ctx, records, _, runs = setup(tmp_path, None)
        cancelled = []
        async def cancel(args):
            cancelled.append(args["preparation_id"])
        ctx.platform.python_env = SimpleNamespace(cancel=cancel)
        records.put("qrun", {"id": "allocation", "state": "queued", "allocation": True})
        records.put("qrun", {"id": "strategy", "objectId": "s", "workspaceId": "ws", "state": "preparing", "stage": "environment",
                             "environment": {"state": "running", "preparationId": "env"}})
        task = asyncio.create_task(asyncio.sleep(30))
        runs.tasks["strategy"] = task
        assert (await runs.cancel("strategy"))["state"] == "cancelled"
        assert task.cancelled() and cancelled == ["env"] and runs.read("allocation")["state"] == "queued"
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["run", "sweep"])
def test_record_write_failure_removes_only_the_new_directory(tmp_path, monkeypatch, kind):
    from modules.quant.backend.object_sweeps import Sweeps
    async def scenario():
        _, records, store, runs = setup(tmp_path, None)
        obj = await new_strategy(store)
        parent = runs.directory("unused").parent
        keep = parent / "other-run"
        keep.mkdir(parents=True)
        def fail(*args):
            raise OSError("catalog write failed")
        monkeypatch.setattr(records, "put", fail)
        with pytest.raises(OSError, match="catalog write failed"):
            if kind == "run":
                await runs.start(obj)
            else:
                await Sweeps(runs).start(obj, {"axes": [{"name": "fast", "values": [19, 20]}]})
        assert list(parent.iterdir()) == [keep]
    asyncio.run(scenario())


def test_cancel_waits_for_the_copy_before_discarding_the_private_directory(tmp_path, monkeypatch):
    async def scenario():
        _, _, store, runs = setup(tmp_path, None)
        obj = await new_strategy(store)
        started, release = asyncio.Event(), asyncio.Event()
        async def copying(obj, directory, source):
            started.set()
            await release.wait()
            (directory / "partial").write_text("copied")
        monkeypatch.setattr(runs, "fix_project", copying)
        task = asyncio.create_task(runs.start(obj))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list(runs.directory("unused").parent.iterdir())
    asyncio.run(scenario())


def test_missing_signal_during_start_discards_the_new_snapshot(tmp_path):
    async def scenario():
        _, _, store, runs = setup(tmp_path, None)
        obj = await new_strategy(store)
        with pytest.raises(ContractError) as caught:
            await runs.start(obj, {"signalOverride": {"path": str(tmp_path / "missing.parquet")}})
        assert caught.value.code == "unavailable" and isinstance(caught.value.__cause__, FileNotFoundError)
        assert not list(runs.directory("unused").parent.iterdir())
    asyncio.run(scenario())
