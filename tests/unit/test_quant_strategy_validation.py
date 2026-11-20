"""Looking at the held-out part: asked for once and tagged, said again when it was looked at before (the same values, other values, other code), carried
along by a copy of the object, short and unset parts. The run itself is a stand-in; the engine is not involved."""
import asyncio

import pytest
from talyx_sdk import ContractError

from tests.fixtures.strategy.fakes import insert_run, services, strategy


def scenario(tmp_path, body):
    async def run():
        ctx, records, shell = services(tmp_path)
        try:
            return await body(ctx, shell)
        finally:
            records.close()
    return asyncio.run(run())


def patch(shell, monkeypatch):
    started = []

    async def start(object_id, args):
        found = insert_run(shell, object_id, "single", runKind="heldout", heldOut=True, of=args["of"], created=9_000 + len(started))
        started.append(args)
        return found

    monkeypatch.setattr(shell.runs, "start", start)
    return started


def set_held(shell, obj, day="2026-05-01"):
    record = shell.store.read(obj)
    data = record["settings"]["data"]
    data.update(heldOutFrom=day, until="2026-10-31")
    from modules.quant.backend.object_store import OBJECT
    shell.store.records.put(OBJECT, record)


def test_held_out_once_and_tagged(tmp_path, monkeypatch):
    async def body(ctx, shell):
        obj = await strategy(shell)
        set_held(shell, obj)
        started = patch(shell, monkeypatch)
        run = insert_run(shell, obj, "single", created=1_000, params={"every": 24})
        from tests.unit.quant_public import public_actions
        act = public_actions(ctx, shell, monkeypatch)
        receipt = await act("validate_held_out", {"runId":run["id"]})
        first = shell.runs.read(receipt["runId"])
        assert started == [{"kind": "heldout", "of": run["id"]}] and first["runKind"] == "heldout" and first["heldOut"] is True
        used = shell.store.read(obj)["heldOutUsed"]
        assert [item["of"] for item in used] == [run["id"]] and used[0]["params"] == {"every": 24}
    scenario(tmp_path, body)


def test_held_out_second_time_warned(tmp_path, monkeypatch):
    async def body(ctx, shell):
        obj = await strategy(shell)
        set_held(shell, obj)
        patch(shell, monkeypatch)
        run = insert_run(shell, obj, "single", created=1_000, params={"every": 24})
        assert (await shell.validation.status(run["id"]))["used"] is None
        await shell.validation.held_out(run["id"])
        same = (await shell.validation.status(run["id"]))["used"]
        assert same and same["same"] is True
        other = insert_run(shell, obj, "single", created=2_000, params={"every": 12})
        different = (await shell.validation.status(other["id"]))["used"]
        assert different and different["same"] is False  # the held-out part was seen all the same
        changed = insert_run(shell, obj, "single", created=3_000, params={"every": 24}, code={"digest": "e" * 64, "entry": "strategy.py"})
        assert (await shell.validation.status(changed["id"]))["used"]["same"] is False
    scenario(tmp_path, body)


def test_held_out_reuse_across_copies(tmp_path, monkeypatch):
    async def body(ctx, shell):
        obj = await strategy(shell)
        set_held(shell, obj)
        patch(shell, monkeypatch)
        run = insert_run(shell, obj, "single", created=1_000)
        await shell.validation.held_out(run["id"])
        copied = await shell.store.copy(obj)
        assert shell.store.read(copied["id"])["heldOutUsed"] == shell.store.read(obj)["heldOutUsed"]
        again = insert_run(shell, copied["id"], "single", created=2_000)
        set_held(shell, copied["id"])
        assert (await shell.validation.status(again["id"]))["used"] is not None
    scenario(tmp_path, body)


def test_held_out_short(tmp_path, monkeypatch):
    async def body(ctx, shell):
        obj = await strategy(shell)
        set_held(shell, obj, "2026-09-20")
        patch(shell, monkeypatch)
        run = insert_run(shell, obj, "single", created=1_000)
        status = await shell.validation.status(run["id"])
        assert status["short"] is True and status["days"] < 60
    scenario(tmp_path, body)


def test_held_out_needs_start(tmp_path, monkeypatch):
    async def body(ctx, shell):
        obj = await strategy(shell)
        record = shell.store.read(obj)
        record["settings"]["data"].pop("heldOutFrom", None)
        from modules.quant.backend.object_store import OBJECT
        shell.store.records.put(OBJECT, record)
        patch(shell, monkeypatch)
        run = insert_run(shell, obj, "single", created=1_000)
        with pytest.raises(ContractError) as caught:
            await shell.validation.status(run["id"])
        assert caught.value.code == "no_held_out_period"
    scenario(tmp_path, body)
