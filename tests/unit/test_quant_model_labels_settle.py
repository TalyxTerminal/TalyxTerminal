"""A published model's record settles: each update writes the realised labels of earlier periods whose horizon has passed (point in time, from the bars of that
update), and the library's figures pick them up. A period whose horizon has not passed stays waiting."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from modules.quant.backend import library_after
from tests.unit.quant_library_fakes import (
    DAY,
    INFO,
    NAMES,
    build,
    close_of,
    factor_spec,
    factor_value,
)

HOLDING = 2
PUBLISHED = 50


def run(coro):
    return asyncio.run(coro)


def realised(world, day):
    """The label this model was trained on: the return over `HOLDING` bars from this one's close."""
    return world.prices.loc[(day + HOLDING) * DAY] / world.prices.loc[day * DAY] - 1


def model_spec():
    spec = factor_spec("Blend", "m1", PUBLISHED, kind="model", holding=HOLDING)
    spec["meta"].update(pipeline={"mode": "standard"}, fields=["prediction", "label"], horizon={"holding": HOLDING, "delay": 1, "ahead": HOLDING + 1},
                        publication={"kind": "model", "name": "Blend", "field": "prediction"})
    return spec


class ModelProducer:
    """What `PipelineProducer` gives for a model: predictions of the new bars, and the labels of earlier periods whose horizon the bars of this update have passed."""

    def __init__(self, world, store):
        self.world, self.store = world, store

    async def make(self, version, *, through, after, now, attempt, **_):
        from modules.quant.backend.library_produce import Made
        days = [day for day in range(0, through // DAY + 1) if self.world.has(day, now)]
        rows = [(day * DAY, name, "prediction", factor_value("m1", day, name)) for day in days if after is None or day * DAY > after for name in NAMES]
        last = max(days)
        made = self.store.frame(version["id"], field="prediction", start=PUBLISHED * DAY + 1, columns=["datetime", "instrument"]).drop_duplicates()
        have = set(self.store.frame(version["id"], field="label", start=PUBLISHED * DAY + 1, columns=["datetime"])["datetime"])
        settled = [(int(row.datetime), row.instrument, "label", float(realised(self.world, int(row.datetime) // DAY)[row.instrument]))
                   for row in made.itertuples() if int(row.datetime) not in have and int(row.datetime) // DAY + HOLDING <= last]
        return Made(rows=pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value"]), present={day * DAY for day in days},
                    prices=self.world.prices.loc[[day * DAY for day in days]], total=len(NAMES),
                    settled=pd.DataFrame(settled, columns=["datetime", "instrument", "field", "value"]))


def published(tmp_path):
    world = build(tmp_path, start_day=PUBLISHED)
    spec = model_spec()
    spec["rows"] = [(name, "prediction", day * DAY, factor_value("m1", day, name), close_of(day), close_of(day), "history") for day in range(1, PUBLISHED + 1) for name in NAMES]
    spec["rows"] += [(name, "label", day * DAY, float(realised(world.world, day)[name]), close_of(day), close_of(day), "history") for day in range(1, PUBLISHED - HOLDING + 1) for name in NAMES]
    world.store.commit_versions([spec])
    world.loop.producer = lambda version: ModelProducer(world.world, world.store)
    return world


def points(world):
    return {point["date"] // DAY: point for point in world.store.after_points("m1")}


def test_the_first_period_settles_with_its_realised_label_and_a_later_one_stays_waiting(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = published(tmp_path)
    first = PUBLISHED + 1
    world.clock["now"] = close_of(first) + DELAY + 60_000
    run(world.loop.cycle())  # an update at day T: the new period has a prediction, no label yet
    assert world.store.frame("m1", field="label", datetime=first * DAY).empty
    assert points(world)[first]["settled"] is False
    later = first + HOLDING + 1  # an update at T + horizon + 1
    world.clock["now"] = close_of(later) + DELAY + 60_000
    run(world.loop.cycle())
    labels = world.store.frame("m1", field="label", datetime=first * DAY, columns=["instrument", "value"]).set_index("instrument")["value"]
    expected = realised(world.world, first)
    assert len(labels) == len(NAMES) and all(abs(labels[name] - expected[name]) < 1e-12 for name in NAMES), "the label the model was trained on, of the bars as they were"
    found = points(world)
    assert found[first]["settled"] is True and found[first]["ic"] is not None, "the first period settled with a rank IC"
    assert found[later]["settled"] is False and found[later - 1]["settled"] is False, "a period whose horizon has not passed stays waiting"
    assert world.store.frame("m1", field="label", datetime=later * DAY).empty


def test_a_label_written_once_is_not_written_again(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = published(tmp_path)
    for day in (PUBLISHED + 1, PUBLISHED + HOLDING + 1, PUBLISHED + HOLDING + 2):
        world.clock["now"] = close_of(day) + DELAY + 60_000
        run(world.loop.cycle())
    rows = world.store.frame("m1", field="label", start=(PUBLISHED + 1) * DAY, columns=["datetime", "instrument"])
    assert not rows.duplicated().any(), "each (period, name) has one label"
    assert set(rows["datetime"] // DAY) == {PUBLISHED + 1, PUBLISHED + 2}, "only the periods whose horizon had passed by the last update"


def test_model_update_asks_for_the_labels_of_the_periods_without_one_and_keeps_only_those(tmp_path):
    from modules.quant.backend import model_library as ML
    world = published(tmp_path)
    # predictions made after publication: days 51..54, the first of them already labelled
    rows = [(name, "prediction", day * DAY, 0.1, close_of(day), close_of(day), "scheduled") for day in range(51, 55) for name in NAMES]
    rows += [(name, "label", 51 * DAY, 0.0, close_of(54), close_of(54), "scheduled") for name in NAMES]
    frame = pd.DataFrame(rows, columns=["instrument", "field", "datetime", "value", "availableAt", "recordedAt", "mode"])
    version = world.store.version("m1")
    world.store.append_values("m1", frame, cursor=54 * DAY)
    version = world.store.version("m1")
    seen = {}
    library = object.__new__(ML.ModelLibrary)
    library.library = SimpleNamespace(store=world.store, version_directory=lambda _id: tmp_path / "version")
    (tmp_path / "version" / "windows" / "0").mkdir(parents=True)

    async def run_model(job, directory, **_):
        seen["job"] = job
        out = Path(job["output"])
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"label": [55 * DAY], "column": [NAMES[0][2:]], "value": [0.5]}).to_parquet(out / "values.parquet", index=False)
        # the research process labels every period it can with the bars it has: here 51..53 (54's horizon has not passed)
        pd.DataFrame([(day * DAY, name[2:], 0.25) for day in range(51, 54) for name in NAMES], columns=["label", "column", "value"]).to_parquet(out / "labels.parquet", index=False)
        return {"ok": True}
    library.models = SimpleNamespace(engine=SimpleNamespace(run_model=run_model), store=None)
    library.store = world.store
    real = ML.P.prepare_inputs
    ML.P.prepare_inputs = lambda *_a, **_k: asyncio.sleep(0, ([], []))
    try:
        values = run(library.model_update(version, {**version["meta"], "params": {}}, {"info": INFO, "barsPath": "b", "extrasPath": "e", "key": "k"}, tmp_path / "work"))
    finally:
        ML.P.prepare_inputs = real
    assert seen["job"]["labelFrom"] == 52 * DAY, "from the first period after publication that has a prediction and no label"
    labels = values.attrs["labels"]
    assert set(labels["datetime"] // DAY) == {52, 53}, "the labelled period 51 is not written again; 54 has none yet"
    assert set(labels["field"]) == {"label"} and set(labels["instrument"]) == set(NAMES)


def test_measure_settles_a_period_only_when_its_label_is_there():
    days = [day * DAY for day in range(1, 8)]
    values = pd.DataFrame([(d, n, float(k)) for d in days for k, n in enumerate(NAMES)], columns=["datetime", "instrument", "value"])
    labels = pd.DataFrame([(d, n, float(k)) for d in days[:3] for k, n in enumerate(NAMES)], columns=["datetime", "instrument", "value"])
    prices = pd.DataFrame(100.0, index=days, columns=NAMES)
    figures, found = library_after.measure(values, prices, scheduled=prices.index[prices.index > 0], holding=HOLDING, delay=1, annualization=252, realised=labels, ahead=HOLDING + 1)
    settled = {point["date"] // DAY: point["settled"] for point in found}
    assert [settled[d] for d in range(1, 8)] == [True, True, True, False, False, False, False]
    assert figures["settled"] == 3 and figures["pending"] == 4


def test_a_catch_up_update_labels_its_own_periods_whose_horizon_has_passed(tmp_path):
    """The first update after publication makes many periods at once (a catch-up): those whose horizon the bars already pass get their labels in the
    same update; asked only for earlier predictions, it asked for nothing and the item showed every period waiting until the next update."""
    from modules.quant.backend import model_library as ML
    world = published(tmp_path)
    version = world.store.version("m1")
    seen = {}
    library = object.__new__(ML.ModelLibrary)
    library.library = SimpleNamespace(store=world.store, version_directory=lambda _id: tmp_path / "version")
    (tmp_path / "version" / "windows" / "0").mkdir(parents=True)

    async def run_model(job, directory, **_):
        seen["job"] = job
        out = Path(job["output"])
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([(day * DAY, name[2:], 0.1) for day in range(51, 55) for name in NAMES], columns=["label", "column", "value"]).to_parquet(out / "values.parquet", index=False)
        pd.DataFrame([(day * DAY, name[2:], 0.25) for day in range(51, 53) for name in NAMES], columns=["label", "column", "value"]).to_parquet(out / "labels.parquet", index=False)
        return {"ok": True}
    library.models = SimpleNamespace(engine=SimpleNamespace(run_model=run_model), store=None)
    library.store = world.store
    real = ML.P.prepare_inputs
    ML.P.prepare_inputs = lambda *_a, **_k: asyncio.sleep(0, ([], []))
    try:
        values = run(library.model_update(version, {**version["meta"], "params": {}}, {"info": INFO, "barsPath": "b", "extrasPath": "e", "key": "k"}, tmp_path / "work"))
    finally:
        ML.P.prepare_inputs = real
    assert seen["job"]["labelFrom"] == version["cursor"] + 1
    assert set(values.attrs["labels"]["datetime"] // DAY) == {51, 52}


def test_a_published_model_is_updated_on_the_versions_own_window_caches(tmp_path):
    """An update reads the version's trained windows where they are and writes the windows it retrains back to the version."""
    from modules.quant.backend import model_library as ML
    world = published(tmp_path)
    version = world.store.version("m1")
    asked = {}
    library = object.__new__(ML.ModelLibrary)
    library.library = SimpleNamespace(store=world.store, version_directory=lambda _id: tmp_path / "version")
    (tmp_path / "version" / "windows" / "0").mkdir(parents=True)
    (tmp_path / "version" / "windows" / "0" / "model.txt").write_text("trained", encoding="utf-8")

    async def run_model(job, directory, **_):
        asked.update(job=job, cached=sorted(path.name for path in Path(job["cache"]).iterdir()))
        out = Path(job["output"])
        out.mkdir(parents=True, exist_ok=True)
        (Path(job["cache"]) / "1").mkdir()  # a window it retrained
        pd.DataFrame({"label": [55 * DAY], "column": [NAMES[0][2:]], "value": [0.5]}).to_parquet(out / "values.parquet", index=False)
        return {"ok": True}
    library.models = SimpleNamespace(engine=SimpleNamespace(run_model=run_model), store=None)
    library.store = world.store
    real = ML.P.prepare_inputs
    ML.P.prepare_inputs = lambda *_a, **_k: asyncio.sleep(0, ([], []))
    try:
        meta = {**version["meta"], "params": {}}
        run(library.model_update(version, meta, {"info": INFO, "barsPath": "b", "extrasPath": "e", "key": "k"}, tmp_path / "update"))
        assert Path(asked["job"]["cache"]) == tmp_path / "version" / "windows" and asked["cached"] == ["0"]
        assert (tmp_path / "version" / "windows" / "1").is_dir(), "the window it retrained is the version's"
    finally:
        ML.P.prepare_inputs = real
