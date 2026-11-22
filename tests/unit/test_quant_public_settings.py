"""Quant edits against saved objects use the same public entry point for every origin."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from talyx_sdk import ContractError, wire

from modules.quant.backend import market_inputs, strategy_settings
from modules.quant.backend.object_routes import handle_actions
from modules.quant.backend.public_inputs import ObjectId
from tests.unit.test_quant_factor_runs import services
from tests.unit.test_quant_object_runs import INSTRUMENT, OKX, SPY


def test_settings_merge_reset_arrays_and_source_equivalence(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)

        async def invoke(name, args, origin="code"):
            return await ctx.handlers[name](
                args, SimpleNamespace(caller=SimpleNamespace(origin=origin))
            )

        created = await invoke(
            "create_object",
            {
                "kind": "strategy",
                "preset": "crossover",
                "params": {"fast": 10, "slow": 100},
                "data": {
                    "symbols": ["SPY", "binance:BTC/USDT:USDT"],
                    "timeframe": "1d",
                    "start": "2020-01-01",
                },
            },
        )
        obj = {"objectId": created["objectId"]}
        assert "heldOutFrom" not in created["settings"]["run"]
        changed = await invoke("update_object", {**obj, "params": {"slow": 150}})
        assert changed["settings"]["params"] == {"fast": 10, "slow": 150}
        assert changed["changed"] == [{"path": "params.slow", "from": 100, "to": 150}]
        changed = await invoke("update_object", {**obj, "params": {"fast": None}})
        assert (
            "fast" not in changed["settings"]["params"]
            and changed["effective"]["params"]["fast"] == 20
        )
        changed = await invoke(
            "update_object",
            {**obj, "data": {"symbols": {"remove": ["SPY"], "add": ["okx:BTC/USDT:USDT"]}}},
        )
        assert changed["settings"]["data"]["symbols"] == [
            "binance:BTC/USDT:USDT",
            "okx:BTC/USDT:USDT",
        ]
        with pytest.raises(ContractError, match=r"run\.heldOutFrom"):
            await invoke("update_object", {**obj, "data": {"heldOutFrom": "2021-01-01"}})
        with pytest.raises(ValidationError):
            ObjectId.model_validate({**obj, "formula": "x"})
        changed = await invoke(
            "update_object", {**obj, "run": {"costs": {"feePct": 0.1}, "capital": 15000}}
        )
        assert changed["settings"]["run"]["costs"]["feePct"] == 0.1
        effective = strategy_settings.effective("perpetual", changed["settings"]["run"], {})
        assert strategy_settings.account_for({}, effective, "perpetual")["fee"][
            "taker"
        ] == pytest.approx(0.001)
        for name, args in [("read_object", obj), ("list_objects", {})]:
            code = wire.encode(await invoke(name, args))
            assert (
                code == wire.encode(await invoke(name, args, "ai")) and len(code.decode()) <= 18_000
            )
        await shell.store.update_settings(created["objectId"], {"params": {"slow": 120}})
        assert shell.store.read(created["objectId"])["settings"]["params"] == {"slow": 120}

    asyncio.run(scenario())


def test_creating_factor_resolves_only_the_selected_universe(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)
        calls = []
        original = ctx.call
        async def market(name, args, **kwargs):
            if name == "market.instruments.get":
                calls.extend(args.get("symbols", []))
                if "^GSPC" in args.get("symbols", []):
                    raise ContractError("not_found", "Market cannot identify ^GSPC")
            return await original(name, args, **kwargs)
        monkeypatch.setattr(ctx, "call", market)
        try:
            create = ctx.handlers["create_object"]
            result = await create({"kind": "factor", "formula": "$close", "data": {"symbols": ["SPY"]}}, None)
            assert result["settings"]["data"] == {"symbols": ["SPY"], "timeframe": "1d", "start": "2016-01-01"}
            assert "^GSPC" not in calls
            with pytest.raises(ContractError, match=r"cannot identify \^GSPC"):
                await create({"kind": "factor", "data": {"start": "2025-01-01"}}, None)
        finally:
            shell.store.records.close()
    asyncio.run(scenario())


def test_model_inputs_and_allocation_percentages(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)
        first = {"source": "builtin", "ref": "alpha158", "name": "Alpha158"}
        second = {"source": "builtin", "ref": "styles", "name": "Styles"}
        model = await ctx.handlers["create_object"](
            {"kind": "model", "params": {"inputs": [first]}}, None
        )
        changed = await ctx.handlers["update_object"](
            {"objectId": model["objectId"], "params": {"inputs": {"add": [second]}}}, None
        )
        assert changed["settings"]["params"]["inputs"] == [first, second]
        created = await ctx.handlers["create_object"](
            {"kind": "allocation", "params": {"maxWeightPct": 40}}, None
        )
        assert created["settings"]["params"]["maxWeightPct"] == 40
        for value in (40, 0.4):
            result = await ctx.handlers["update_object"](
                {"objectId": created["objectId"], "params": {"maxWeightPct": value}}, None
            )
            assert result["settings"]["params"]["maxWeightPct"] == value
            assert (
                float(shell.store.read(created["objectId"])["settings"]["params"]["max_weight"])
                / 100
                == value / 100
            )

    asyncio.run(scenario())


def test_unknown_saved_identity_is_submitted_once_and_reused_after_registration():
    class OtherDirectory:
        def __init__(self):
            self.registered = False
            self.calls = []

        async def call(self, name, args):
            self.calls.append(args)
            assert name == "market.instruments.get"
            if "records" in args:
                assert args["records"] == [SPY]
                self.registered = True
            status = "redirect" if self.registered else "not_found"
            return {
                "items": [
                    {
                        "status": status,
                        "symbol": "US:SPY",
                        "instrument": SPY if self.registered else None,
                    }
                ],
                "nextCursor": None,
            }

    async def scenario():
        ctx = OtherDirectory()
        assert await market_inputs.current(ctx, SPY) == SPY
        assert await market_inputs.current(ctx, SPY) == SPY
        assert [set(a) - {"limit"} for a in ctx.calls] == [{"symbols"}, {"records"}, {"symbols"}]

    asyncio.run(scenario())


def test_returning_reused_code_keeps_original_instrument(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)
        obj = await shell.store.create({"kind": "model", "name": "Historical"})
        await shell.store.update_settings(
            obj["id"], {"data": {"symbols": [{"instrument": INSTRUMENT}]}}
        )
        calls = []

        async def lookup(name, args):
            assert name == "market.instruments.get"
            calls.append(args["symbols"])
            return {
                "items": [
                    {
                        "status": "found",
                        "symbol": "DASH",
                        "instrument": INSTRUMENT if s == INSTRUMENT["instrumentId"] else OKX,
                    }
                    for s in args["symbols"]
                ],
                "nextCursor": None,
            }

        ctx.call = lookup
        result = await ctx.handlers["update_object"](
            {"objectId": obj["id"], "data": {"symbols": ["DASH"]}}, None
        )
        assert result["settings"]["data"]["symbols"] == ["DASH"]
        assert shell.store.read(obj["id"])["settings"]["data"]["symbols"] == [
            {"instrument": INSTRUMENT}
        ]
        assert all("DASH" not in ids for ids in calls)

    asyncio.run(scenario())


def test_maotai_ex_date_matches_the_exchange_day():
    from modules.quant.backend.public_settings import milliseconds
    from modules.quant.backend.research_calendar import label_of

    # The Shanghai market's ex date begins while the UTC date is still June 25.
    stock = market_inputs.stock_times(
        {
            "stock": {
                "sessions": [],
                "corporateActions": [
                    {"effectiveTime": "2026-06-26T00:00:00+08:00", "publishedAt": None}
                ],
            }
        }
    )
    effective = stock["stock"]["corporateActions"][0]["effectiveTime"]
    assert label_of(effective, "1d", "Asia/Shanghai") == milliseconds("2026-06-26")
    assert label_of(effective, "1d", "Asia/Shanghai") == label_of(
        milliseconds("2026-06-26T09:30:00+08:00"), "1d", "Asia/Shanghai"
    )


def test_a_failed_combined_rename_and_settings_edit_keeps_original_object(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)
        obj = await ctx.handlers["create_object"](
            {"kind": "strategy", "name": "Original", "preset": "crossover"}, None
        )
        with pytest.raises(ContractError):
            await ctx.handlers["update_object"](
                {"objectId": obj["objectId"], "name": "", "params": {"fast": 30}}, None
            )
        assert shell.store.read(obj["objectId"])["settings"]["params"] == {}

    asyncio.run(scenario())


def test_activity_ends_with_the_default_public_run_and_frozen_dates(tmp_path, monkeypatch):
    from tests.unit.test_quant_factor_runs import DATA, DAY, prepared, settled

    async def scenario():
        ctx, shell, factors = services(tmp_path, monkeypatch, [0.012])

        async def bars(settings, report=None, facts=True):
            result = await prepared(settings, report)
            return {**result, "bars": 2, "dataFrom": 0, "dataTo": DAY}

        monkeypatch.setattr(factors.data, "prepare", bars)
        handle_actions(ctx, shell)
        obj = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(obj["id"], {"data": DATA})
        await ctx.handlers["update_object"](
            {"objectId": obj["id"], "formula": "-($close / Ref($close, 5) - 1)"}, None
        )
        receipt = await ctx.handlers["start_run"]({"objectId": obj["id"]}, None)
        await settled(factors, receipt["runId"])
        process = ctx.sdk.process
        async with asyncio.timeout(5):
            while process.activities:
                await asyncio.sleep(0.01)
        summary = await ctx.handlers["read_run"]({"runId": receipt["runId"]}, None)
        events = [
            wire.decode(body)
            for event, body in zip(process.sent, process.bodies, strict=True)
            if event["t"] == "activity_end"
            and (process.targets[event["activity"]] or {}).get("objectId")
            == "run:" + receipt["runId"]
        ]
        assert events == [summary]
        assert summary["tested"] == {
            "start": "1970-01-01",
            "end": "1970-01-02",
            "bars": 2,
            "timeframe": DATA["timeframe"],
        }
        assert summary["numbers"]["rankIc"] == 0.012 and summary["symbols"]["used"] == 1

    asyncio.run(scenario())


def test_saved_identity_batches_follow_each_cursor_and_repair_only_missing_ids():
    identities = [f"id-{i}" for i in range(607)]
    records = [{"instrumentId": item} for item in identities]
    missing = {"id-498", "id-501"}
    requests = []

    async def get(action, args):
        assert action == "market.instruments.get"
        key = "symbols" if "symbols" in args else "records"
        chunk = args[key]
        assert len(chunk) <= 500
        cursor = int(args.get("cursor", 0))
        requests.append((key, len(chunk), cursor))
        values = chunk[cursor : cursor + 200]
        if key == "records":
            assert [r["instrumentId"] for r in values] == ["id-498", "id-501"]
            values = [r["instrumentId"] for r in values]
        items = [
            {
                "status": "not_found" if key == "symbols" and item in missing else "found",
                "symbol": item,
                "instrument": {"instrumentId": item},
            }
            for item in values
        ]
        return {
            "items": items,
            "nextCursor": str(cursor + len(items)) if cursor + len(items) < len(chunk) else None,
        }

    resolved = asyncio.run(market_inputs.saved_items(SimpleNamespace(call=get), records))
    assert [r["symbol"] for r in resolved] == identities
    assert requests == [
        ("symbols", 500, 0),
        ("symbols", 500, 200),
        ("symbols", 500, 400),
        ("symbols", 107, 0),
        ("records", 2, 0),
    ]


GOOD = '''import talyx_quant as q


class Hold(q.Strategy):
    def __init__(self):
        super().__init__()
        self.size = q.param("size", 1.0, min=0, label="Size")

    def on_bar(self, bar):
        pass
'''


def test_code_is_saved_and_checked_without_running(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)

        async def invoke(name, args):
            return await ctx.handlers[name](args, SimpleNamespace(caller=SimpleNamespace(origin="ai")))

        created = await invoke("create_object", {"kind": "strategy", "code": GOOD, "data": {
            "symbols": ["SPY"], "timeframe": "1d", "start": "2020-01-01"}})
        assert created["codeErrors"] == [] and created["effective"]["params"]["size"] == 1.0
        obj = {"objectId": created["objectId"]}
        with pytest.raises(ContractError, match="no setting"):
            await invoke("update_object", {**obj, "code": GOOD.replace("1.0", "2.0"), "params": {"typo": 1}})
        unchanged = await invoke("read_object", obj)
        assert unchanged["version"] == created["version"] and unchanged["effective"] == created["effective"]
        assert shell.store.code(shell.store.read(obj["objectId"])) == GOOD
        await invoke("update_object", {**obj, "params": {"size": 2}})
        text_param = GOOD.replace('1.0, min=0', '"small"')
        for invalid_source in (text_param, GOOD.replace("min=0", "min=0, max=1")):
            original = await invoke("read_object", obj)
            with pytest.raises(ContractError):
                await invoke("update_object", {**obj, "expectedVersion": original["version"], "code": invalid_source})
            assert await invoke("read_object", obj) == original
            assert shell.store.code(shell.store.read(obj["objectId"])) == GOOD
        fixed = await invoke("update_object", {**obj, "code": text_param, "params": {"size": None}})
        assert fixed["effective"]["params"] == {"size": "small"} and fixed["codeErrors"] == []
        broken = await invoke("update_object", {"objectId": created["objectId"], "code": GOOD.replace("pass", "pass(")})
        assert broken["codeErrors"][0]["file"] == "strategy.py" and broken["effective"]["params"] is None

    asyncio.run(scenario())


def test_concurrent_rename_prevents_stale_source_write(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        handle_actions(ctx, shell)
        obj = await ctx.handlers["create_object"]({"kind": "strategy", "code": GOOD}, None)
        entered, proceed = asyncio.Event(), asyncio.Event()

        async def open_project(record):
            entered.set()
            await proceed.wait()
            return None

        monkeypatch.setattr(shell.store, "open_in_developer", open_project)
        args = {"objectId": obj["objectId"], "expectedVersion": obj["version"]}
        edit = asyncio.create_task(ctx.handlers["update_object"]({**args, "code": GOOD + "# stale"}, None))
        await entered.wait()
        winner = await ctx.handlers["update_object"]({**args, "name": "Winner"}, None)
        proceed.set()
        with pytest.raises(ContractError, match="current object"):
            await edit
        assert shell.store.code(shell.store.read(obj["objectId"])) == GOOD
        assert winner["version"] == obj["version"] + 1

    asyncio.run(scenario())
