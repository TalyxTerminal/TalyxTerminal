"""The library's ledger under random days on a fake clock: closing and reopening TALYX, a source that gives bars late or never, failing code,
cancelled and retried updates, new versions, a research object switching versions and versions stopped and resumed. After every step the
accounts that must always hold are checked: each period goes in once; a value is never dated before its bar closed nor before it was made
when it was late; a model never reuses an input's older value; the figures after publication count settled periods only. (A one-off adjustment read only once is the allocation's, A3, S5.) Added with the new store: an archived version reads nothing and only a stopped one nobody uses
can be archived (A9); the item summaries equal a recount (A10); a publication stopped by future data or failing leaves nothing (A11); a published model
holds every factor version it reads and a research object holds the version it pins (A12)."""
import asyncio

import numpy as np
import pytest
from talyx_sdk import ContractError

from tests.unit.quant_library_fakes import (
    DAY,
    HOUR,
    NAMES,
    build,
    close_of,
    factor_spec,
    model_publishing,
    publish_and_wait,
    publishing,
    reopen,
)

STEPS = 48


def check(world, pinned):
    store = world.store
    for version in store.all_versions():
        if version["archived_at"] is not None:
            continue
        meta = version["meta"]
        through = meta["publishedThrough"]
        values = store.frame(version["id"])
        assert not values.duplicated(["datetime", "instrument", "field"]).any(), f"{version['id']}: a period went in twice"
        counted = values.groupby("datetime")["instrument"].nunique()
        assert store.days(version["id"]) == [(int(label), int(names)) for label, names in counted.items()], "the day counts follow the values"
        made = values[values["mode"] != "history"]
        assert (made["datetime"] > through).all(), "nothing after publication rewrites the published history"
        closes = made["datetime"].map(lambda label: close_of(label // DAY))
        assert (made["recordedAt"] >= closes).all() and (made["availableAt"] >= closes).all(), "no value is dated before its bar closed"
        on_time, late = made[made["mode"] == "scheduled"], made[made["mode"] == "catch_up"]
        assert (on_time["availableAt"] == on_time["datetime"].map(lambda label: close_of(label // DAY))).all()
        assert (late["availableAt"] == late["recordedAt"]).all(), "a late value is dated when it was made, so nothing could have used it earlier"
        periods = store.periods(version["id"])
        labels = [period["label"] for period in periods]
        assert len(labels) == len(set(labels)), "one row per period"
        produced = {period["label"] for period in periods if period["result"] in ("ok", "late")}
        assert produced == set(made["datetime"].astype("int64")), f"{version['id']}: a period counted as made has values and only those"
        inputs = meta.get("inputs") or []
        if inputs:
            source = store.frame(inputs[0]["versionId"]).set_index(["datetime", "instrument"])
            for row in made.itertuples():
                assert (row.datetime, row.instrument) in source.index, "a model's value at a bar reads its input's value at that very bar"
                held = source.loc[(row.datetime, row.instrument)]
                assert row.value == pytest.approx(2 * held["value"]) and held["recordedAt"] <= row.recordedAt
            for period in periods:
                if period["result"] == "up":
                    assert period["label"] not in set(made["datetime"]) and "has no" in period["why"] or "archived" in period["why"]
        after, points = version["after"], store.after_points(version["id"])
        if after is not None:
            holding, delay = meta["horizon"]["holding"], meta["horizon"]["delay"]
            settled = [point for point in points if point["settled"]]
            assert all(point["date"] // DAY + delay + holding <= version["cursor"] // DAY for point in settled), "a settled period's holding has ended"
            assert after["settled"] == sum(1 for point in settled if point["after"] and point["ic"] is not None)
            assert all(point["date"] > through for point in points if point["after"])
    # A9: an archived version reads nothing, its record stays; A12: who holds what.
    for version in store.all_versions():
        if version["archived_at"] is not None:
            assert store.frame(version["id"]).empty and store.days(version["id"]) == [] and version["meta"].get("published") is not None
            continue
        for entry in (version["meta"].get("pipeline") or {}).get("reads", []) or [item["versionId"] for item in version["meta"].get("inputs") or []]:
            held = {user["owner_id"] for user in store.users(entry)}
            assert "qlib:" + version["id"] in held, f"{version['id']} holds {entry}, which it reads"
    assert "qobject:s1" in {user["owner_id"] for user in store.users(pinned)}, "a research object holds the version it pins"
    # A10: the summaries are what a recount gives.
    shown = {row["itemId"]: (row["state"], row["latestPeriod"], row["last20"], row["users"]) for row in store.summaries()}
    store.resummarize()
    assert shown == {row["itemId"]: (row["state"], row["latestPeriod"], row["last20"], row["users"]) for row in store.summaries()}, "summaries equal their recount"


async def settle(world):
    for _ in range(3):
        running = [entry["task"] for entry in list(world.loop.running.values())]
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        await asyncio.sleep(0)


async def scenario(world, rng, tmp_path):
    from modules.quant.backend.library_loop import DELAY
    store = world.store
    store.commit_versions([factor_spec("Breadth", "f1", 59, holding=2)])
    store.commit_versions([factor_spec("Value", "g1", 59, holding=1)])
    store.commit_versions([{**factor_spec("Blend", "m1", 59, kind="model", holding=1, inputs=[{"versionId": "f1", "name": "Breadth", "version": 1}]), "holds": [{"versionId": "f1"}]}])
    pinned, factors, made = "m1", ["f1"], 1
    world.library.acquire(pinned, "qobject:s1", "ws", kind="strategy", name="Blend top 20", how="research, pinned v1")
    done = []
    for _step in range(STEPS):
        today = world.clock["now"] // DAY
        choice = rng.choice(["punctual", "punctual", "punctual", "tick", "late", "gap", "fail", "restart", "cancel", "retry", "new", "switch", "stop", "resume",
                             "auto_off", "auto_on", "archive", "blocked_publish", "publish_model"])
        done.append(choice)
        if choice == "punctual":
            # The loop looks right after the next bar is due, as it does while TALYX is open.
            world.clock["now"] = max(world.clock["now"], close_of(today) + DELAY) + (DAY if world.clock["now"] >= close_of(today) + DELAY else 0) + 60_000
            await world.loop.cycle()
            for _ in range(int(rng.integers(0, 4))):
                world.clock["now"] += 2 * 60_000 + int(rng.integers(0, 10)) * 60_000
                await world.loop.cycle()
        elif choice == "tick":
            world.clock["now"] += int(rng.integers(1, 30)) * HOUR
            await world.loop.cycle()
        elif choice == "late":
            world.world.late[today + 1] = int(rng.integers(1, 9))
        elif choice == "gap":
            world.world.gaps.add(today + int(rng.integers(1, 4)))
        elif choice == "fail":
            world.world.failing[rng.choice(["g1", *factors, "m1"])] = world.clock["now"] + int(rng.integers(1, 48)) * HOUR
        elif choice == "restart":
            world.clock["now"] += int(rng.integers(1, 73)) * HOUR
            reopen(world)
            store = world.store
            await world.loop.cycle()
        elif choice == "cancel":
            target = rng.choice(["g1", *factors])
            version = store.version(target)
            if version["state"] in ("updating", "failed") and version["auto"] and target not in world.loop.running:
                world.world.gate = asyncio.Event()
                world.loop.retry_now(target)
                for _ in range(20):
                    await asyncio.sleep(0)
                if target in world.loop.running:
                    await world.loop.cancel(target)
                world.world.gate.set()
                world.world.gate = None
                await settle(world)
        elif choice == "retry":
            for version in store.all_versions():
                if version["state"] == "failed" and version["auto"]:
                    world.loop.retry_now(version["id"])
            await settle(world)
        elif choice == "new":
            made += 1
            identity = f"f{made}"
            through = max(59, today - 1)
            store.commit_versions([factor_spec("Breadth", identity, through, holding=2, item_seed="Breadth")])
            factors.append(identity)
        elif choice == "switch":
            target = rng.choice(factors)
            if store.version(target)["state"] != "delisted" and store.version(target)["archived_at"] is None and target != pinned:
                world.library.release(pinned, "qobject:s1")
                world.library.acquire(target, "qobject:s1", "ws", kind="strategy", name="Blend top 20", how="research, pinned")
                pinned = target
        elif choice == "stop":
            target = rng.choice(["g1", *factors, "m1"])
            if store.version(target)["state"] in ("updating", "failed", "expired", "fixed"):
                world.library.stop(target)
        elif choice == "resume":
            stopped = [version["id"] for version in store.all_versions() if version["state"] == "delisted" and version["archived_at"] is None]
            if stopped:
                world.library.resume(rng.choice(stopped))
        elif choice == "auto_off":
            target = rng.choice(["g1", *factors])
            if store.version(target)["state"] in ("updating", "failed", "expired") and store.version(target)["auto"] and target != "m1":
                world.library.set_auto(target, False)
        elif choice == "auto_on":
            off = [version["id"] for version in store.all_versions() if not version["auto"] and version["state"] in ("updating", "failed", "expired") and version["archived_at"] is None]
            if off:
                world.library.set_auto(rng.choice(off), True)
        elif choice == "archive":
            stopped = [version["id"] for version in store.all_versions() if version["state"] == "delisted" and version["archived_at"] is None]
            running = [version["id"] for version in store.all_versions() if version["state"] == "updating"]
            if running:
                with pytest.raises(ContractError):
                    await world.library.archive(running[0])  # only a stopped version can be archived
            for target in stopped:
                if store.users(target):
                    with pytest.raises(ContractError):
                        await world.library.archive(target)  # and only one nobody uses
                else:
                    await world.library.archive(target)
                    break
        elif choice == "blocked_publish":
            # A11: a publication stopped by future data (or any failure) leaves the library as it was.
            before = ([item["id"] for item in store.all_versions()], len(store.summaries()), sorted(path.name for path in store.values_root.glob("*")))
            attempt = publishing(tmp_path / f"publish-{len(done)}", world=world)
            attempt.engine.future_at, attempt.engine.found = 2, {"instrument": "AAPL", "withAll": 1.0, "whenCut": 2.0, "changed": 3}
            await publish_and_wait(attempt, name=f"Blocked {len(done)}")
            assert before == ([item["id"] for item in store.all_versions()], len(store.summaries()), sorted(path.name for path in store.values_root.glob("*")))
        elif choice == "publish_model" and "publish_model" not in done[:-1]:
            setup = model_publishing(world, tmp_path / "model-publish")
            identity, publish = setup.models.new_run("Pipeline model", ["Pipeline factor one", "Pipeline factor two"])
            created, _folders = await setup.library.commit("pub-model", setup.models.read(identity), setup.world.ready, publish)
            factors.append(created[0]["versionId"])
        check(world, pinned)
    return done


@pytest.mark.parametrize("seed", range(12))
def test_the_ledger_holds_under_random_days(tmp_path, seed):
    world = build(tmp_path, seed=seed, start_day=60)
    done = asyncio.run(scenario(world, np.random.default_rng(seed), tmp_path))
    assert done.count("punctual") + done.count("tick") + done.count("restart") > 5
    made = world.store.frame("g1")
    assert len(made) > 59 * len(NAMES) or world.store.version("g1")["state"] != "updating", "the independent factor kept producing"


def test_library_updates_take_their_slots_from_the_run_queue(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=60)
    for index in range(3):
        world.store.commit_versions([factor_spec(f"F{index}", f"v{index}", 60)])
    world.clock["now"] = close_of(61) + DELAY + 60_000
    asyncio.run(world.loop.cycle())
    slots = world.queue.slots
    assert sorted(slot["owner"] for slot in slots) == ["library:v0", "library:v1", "library:v2"]
    assert all(slot["interactive"] is False and isinstance(slot["memory"], (int, float)) and slot["memory"] > 0 for slot in slots)
    from modules.quant.backend import library_loop
    assert not hasattr(library_loop, "CONCURRENT"), "how many run at once is the machine's, not a constant"


def test_a_one_off_adjustment_is_read_once():
    pytest.skip("The allocation's (segment A): its one-off adjustment is read once; the library only keeps the name of the test")


def test_an_allocation_updates_only_on_its_rebalance_days():
    """A monthly allocation is run once a month; the days between carry the last weights and start no research process."""
    from modules.quant.backend import library_produce
    meta = {"producer": "run", "publication": {"kind": "allocation"}, "rebalance": {"every": "month"}}
    first_of_month, middle = 1_704_067_200_000, 1_704_067_200_000 + 14 * DAY  # 2024-01-01 and 2024-01-15
    assert library_produce.rebalances(meta, first_of_month, previous=first_of_month - 31 * DAY)
    assert not library_produce.rebalances(meta, middle, previous=first_of_month)
