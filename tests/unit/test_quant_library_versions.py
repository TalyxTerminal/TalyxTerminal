"""What a version says of itself to the things that read it: a published model's training cut-off and held-out start, and a strategy reading a
model's values the way it reads a factor's."""
import asyncio

from tests.unit.quant_library_fakes import DAY, NAMES, build, factor_spec

SOURCES = [{"marketInstrument": {"instrumentId": NAMES[0]}, "nativeInstrument": "A.X"}]


def model_spec(name="Blend", version_id="m1", through_day=50, **pipeline):
    spec = factor_spec(name, version_id, through_day, kind="model", inputs=[])
    spec["meta"]["fields"] = ["prediction"]
    spec["meta"]["pipeline"] = {"kind": "model", "reads": [], **pipeline}
    spec["rows"] = [(NAMES[0], "prediction", day * DAY, 0.1 * day, (day + 1) * DAY, 99 * DAY, "history") for day in range(1, through_day + 1)]
    return spec


def test_model_version_detail_has_cutoff_and_held_out(tmp_path):
    world = build(tmp_path)
    world.store.commit_versions([model_spec(trainedThrough=40 * DAY, heldOutFrom="1970-02-10")])
    world.store.commit_versions([factor_spec("Breadth", "f1", 50)])
    model, factor = world.library.read_version("m1"), world.library.read_version("f1")
    assert (model["trainingCutoff"], model["heldOutFrom"]) == (40 * DAY, "1970-02-10")
    assert (factor["trainingCutoff"], factor["heldOutFrom"]) == (None, None), "only a model has a training cut-off"
    world.store.commit_versions([model_spec("Bare run", "m2", trainedThrough=None, heldOutFrom=None)])
    assert (world.library.read_version("m2")["trainingCutoff"], world.library.read_version("m2")["heldOutFrom"]) == (None, None)


def test_strategy_reads_a_models_values_like_a_factor(tmp_path):
    from modules.quant.backend import library_inputs
    world = build(tmp_path)
    world.store.commit_versions([model_spec()])
    assert world.library.read_version("m1")["carriesValues"] is True
    prepared = asyncio.run(library_inputs.prepare(world.library, [("signal", "Blend")], SOURCES, DAY, 100 * DAY, "1d", tmp_path / "values.parquet"))
    item, = prepared["items"]
    assert (item["name"], item["fields"], item["rows"], item["instruments"]) == ("Blend", ["prediction"], 50, 1)
    pinned = asyncio.run(library_inputs.prepare(world.library, [("signal", "Blend")], SOURCES, DAY, 100 * DAY, "1d", tmp_path / "pinned.parquet", versions={"signal": "m1"}))
    assert pinned["items"][0]["versionId"] == "m1"


def test_a_deleted_object_lets_go_of_its_library_versions_so_a_stopped_one_can_be_archived(tmp_path, monkeypatch):
    """Archiving needs a stopped version nobody uses; a research object that was deleted is nobody (it no longer lists among the users)."""
    import asyncio

    from tests.unit.quant_library_fakes import factor_spec
    from tests.unit.test_quant_factor_runs import services
    _, shell, _ = services(tmp_path, monkeypatch, [])
    library = shell.library
    library.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    created = asyncio.run(shell.store.create({"kind": "strategy", "name": "Momentum top 50"}))
    library.acquire("v1", "qobject:" + created["id"], "ws", kind="strategy", name="Momentum top 50", how="research, pinned v1")
    assert [user["objectId"] for user in library.users("v1")] == [created["id"]]
    shell.store.delete(created["id"])
    assert library.users("v1") == []
    assert library.stop("v1")["stopped"]
    assert asyncio.run(library.archive("v1"))["archived"]
