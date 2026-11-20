"""A model published into the library with the factors it uses, on the new store: one transaction, the factors held by the model, the model's
updates made by the producer and written by the loop (a missing input is "upstream missing", never an old value)."""
import asyncio

import pytest
from talyx_sdk import ContractError

from tests.unit.quant_library_fakes import (
    DAY,
    PUBLISH_DAYS,
    build,
    close_of,
    factor_spec,
    model_publishing,
)


def run(coro):
    return asyncio.run(coro)


async def commit(setup, name="Blend", factors=("Value", "Size"), *, auto=True):
    identity, publish = setup.models.new_run(name, list(factors))
    publish["autoUpdate"] = auto
    source = setup.models.read(identity)
    created, _folders = await setup.library.commit("pub-" + name, source, setup.world.ready, publish)
    return created


def test_model_and_its_factors_commit_in_one_transaction_on_the_new_store(tmp_path):
    world = build(tmp_path)
    setup = model_publishing(world, tmp_path)
    world.store.commit_versions([factor_spec("Dup", "d1", 50, kind="model")])
    created = run(commit(setup))
    assert [item["version"] for item in created] == [1, 1, 1] and world.library.root.name == "library2"
    names = {world.store.item(item["itemId"])["name"]: item["versionId"] for item in created}
    assert set(names) == {"Value", "Size", "Blend"} and world.store.find_item("Blend")[0]["kind"] == "model"
    model = world.store.version(names["Blend"])
    assert model["state"] == "updating" and model["auto"], "auto is its own switch; a model that updates is state updating"
    for factor in ("Value", "Size"):
        owners = {user["owner_id"] for user in world.store.users(names[factor])}
        assert "qlib:" + names["Blend"] in owners, "the model's version holds each factor version it reads"
    assert [entry["versionId"] for entry in model["meta"]["inputs"]] == [names["Value"], names["Size"]], "what the loop waits on"
    assert {change["version"]["versionId"] for _id, change in setup.models.saved if "version" in change} == set(names.values())
    # A failing commit adds nothing: the second factor's name is another kind in the library.
    versions, files = len(world.store.all_versions()), sorted(path.name for path in world.store.values_root.glob("*"))
    with pytest.raises(ContractError, match="already a model"):
        run(commit(setup, "Other", ("Fresh", "Dup")))
    assert len(world.store.all_versions()) == versions and sorted(path.name for path in world.store.values_root.glob("*")) == files
    assert world.store.find_item("Fresh") == [] and world.store.find_item("Other") == []


def test_a_published_model_with_auto_off_is_still_a_producing_version(tmp_path):
    world = build(tmp_path)
    setup = model_publishing(world, tmp_path)
    created = run(commit(setup, auto=False))
    model = world.store.version(created[-1]["versionId"])
    assert model["state"] == "updating" and not model["auto"] and world.store.summary_of(created[-1]["itemId"])["state"] == "auto_off"


def test_model_update_waits_as_upstream_missing_and_never_reuses_an_old_input(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=PUBLISH_DAYS)
    setup = model_publishing(world, tmp_path)
    created = run(commit(setup))
    by_name = {world.store.item(item["itemId"])["name"]: item["versionId"] for item in created}
    world.world.failing[by_name["Value"]] = 10 ** 15
    world.clock["now"] = close_of(PUBLISH_DAYS + 1) + DELAY + 60_000
    run(world.loop.cycle())
    period = world.store.periods(by_name["Blend"])[-1]
    assert (period["label"], period["result"]) == ((PUBLISH_DAYS + 1) * DAY, "up") and "Value v1 has no" in period["why"]
    assert world.store.frame(by_name["Blend"], datetime=(PUBLISH_DAYS + 1) * DAY).empty, "no value made from Value's value of the day before"
    assert world.store.periods(by_name["Size"])[-1]["result"] == "ok"


def test_the_pipeline_producer_gives_values_and_writes_nothing(tmp_path):
    from modules.quant.backend.library_produce import Made, PipelineProducer
    world = build(tmp_path)
    setup = model_publishing(world, tmp_path)
    created = run(commit(setup))
    factor = world.store.version(created[0]["versionId"])
    before = (len(world.store.read_values(factor["id"])), len(world.store.updates(version_id=factor["id"])))
    setup.models.data = type("Data", (), {"prepare": staticmethod(lambda data, **_: asyncio.sleep(0, world.ready))})()
    made = run(PipelineProducer(setup.library).make(factor, through=PUBLISH_DAYS * DAY, after=100 * DAY, now=close_of(PUBLISH_DAYS) + 6 * 60_000, attempt=0))
    assert isinstance(made, Made) and set(made.rows["field"]) == {"value"} and made.rows["datetime"].min() == 101 * DAY and set(made.rows["instrument"]) <= {"i:" + c for c in "ABCDEFGH"}
    assert len(made.rows) == 20 * 8 and PUBLISH_DAYS * DAY in made.present
    assert (len(world.store.read_values(factor["id"])), len(world.store.updates(version_id=factor["id"]))) == before, "the producer writes nothing; the loop does"


def test_the_pipeline_producer_refuses_when_an_input_has_not_reached_the_period(tmp_path):
    from modules.quant.backend.library_produce import PipelineProducer, UpdateFailure
    world = build(tmp_path)
    setup = model_publishing(world, tmp_path)
    created = run(commit(setup))
    model = world.store.version(created[-1]["versionId"])
    setup.models.data = type("Data", (), {"prepare": staticmethod(lambda data, **_: asyncio.sleep(0, world.ready))})()
    with pytest.raises(UpdateFailure) as refused:
        run(PipelineProducer(setup.library).make(model, through=(PUBLISH_DAYS + 1) * DAY, after=PUBLISH_DAYS * DAY, now=close_of(PUBLISH_DAYS + 1), attempt=0))
    assert refused.value.details["code"] == "up" and "Value" in refused.value.message
