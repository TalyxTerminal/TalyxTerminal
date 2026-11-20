"""Wrong numbers the old library gave, pinned so they do not come back: a published model read its factor's value of the day before; a value
that waited in the queue was called late; figures after publication counted periods whose holding had not ended; a newer version stopped
the older one; a model carried an input's last value forward when the input had none for the bar."""
import asyncio

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import library_summary
from tests.unit.quant_library_fakes import DAY, NAMES, build, close_of, factor_spec


def test_a_models_update_reads_the_factors_value_of_the_same_day_even_when_that_value_was_made_after_the_close():
    from modules.quant.backend.library_loop import DELAY
    from modules.quant.native.library_features import at_closes
    bars = pd.DataFrame({"label": [DAY, 2 * DAY], "column": "A", "closeAt": [close_of(1), close_of(2)], "knownAt": [close_of(1), close_of(2)]})
    rows = pd.DataFrame({"name": "F", "field": "value", "column": "A", "datetime": [DAY, 2 * DAY], "value": [1.0, 2.0],
                         "availableAt": [close_of(1), close_of(2) + DELAY]})
    # Training reads values as they could be known: the day-2 value came after day 2's close, so day 2 sees day 1's value.
    assert at_closes(rows, bars).loc[(2 * DAY, "A"), "F:value"] == 1.0
    # An update running after the factor's own update reads the day-2 value for day 2 (the old library read 1.0 here).
    assert at_closes(rows, bars, known_by=close_of(2) + DELAY + 60_000).loc[(2 * DAY, "A"), "F:value"] == 2.0
    # A value not known yet when the update runs is never used.
    assert at_closes(rows, bars, known_by=close_of(2) + 60_000).loc[(2 * DAY, "A"), "F:value"] == 1.0


def test_an_update_that_waited_in_the_queue_behind_others_is_on_time(tmp_path):
    from modules.quant.backend.library_loop import DELAY, GRACE
    world = build(tmp_path, start_day=60)
    for index in range(3):
        world.store.commit_versions([factor_spec(f"F{index}", f"v{index}", 60)])
    world.clock["now"] = close_of(61) + DELAY + 60_000

    async def scenario():
        world.world.gate = asyncio.Event()
        round_ = asyncio.create_task(world.loop.cycle())
        for _ in range(20):
            await asyncio.sleep(0)
        world.clock["now"] += 4 * GRACE  # the first two updates take twenty minutes; the third waits for a free slot
        world.world.gate.set()
        await round_
    asyncio.run(scenario())
    for index in range(3):
        period = world.store.periods(f"v{index}")[-1]
        assert (period["label"], period["result"]) == (61 * DAY, "ok")
        values = world.store.frame(f"v{index}", datetime=61 * DAY)
        assert set(values["availableAt"]) == {close_of(61)} and set(values["mode"]) == {"scheduled"}


def test_figures_after_publication_count_only_periods_whose_holding_has_ended():
    from modules.quant.backend import library_after
    labels = [day * DAY for day in range(1, 31)]
    rng = np.random.default_rng(3)
    prices = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, .02, (30, len(NAMES))), axis=0)), index=labels, columns=NAMES)
    values = pd.DataFrame([(label, name, float(rng.normal())) for label in labels for name in NAMES], columns=["datetime", "instrument", "value"])
    figures, points = library_after.measure(values, prices, scheduled=prices.index[prices.index > 20 * DAY], holding=2, delay=1, annualization=252)
    # Days 21 to 30 are after publication; a day is settled once the bar delay + holding after it exists, so days 28 to 30 are still open.
    assert figures["settled"] == 7 and figures["pending"] == 3
    assert [point["date"] // DAY for point in points if point["after"] and not point["settled"]] == [28, 29, 30]
    settled_ic = [point["ic"] for point in points if point["after"] and point["settled"]]
    assert figures["value"] == pytest.approx(np.mean(settled_ic))


def test_a_newer_version_does_not_stop_the_older_and_a_model_never_carries_an_inputs_older_value_forward(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=60)
    world.store.commit_versions([factor_spec("F", "f1", 60)])
    world.store.commit_versions([factor_spec("M", "m1", 60, kind="model", inputs=[{"versionId": "f1", "name": "F", "version": 1}])])
    world.store.commit_versions([factor_spec("F", "f2", 60, item_seed="F")])
    assert {version["id"] for version in world.store.continuing()} == {"f1", "f2", "m1"}
    world.library.stop("f1")
    world.clock["now"] = close_of(61) + DELAY + 60_000
    asyncio.run(world.loop.cycle())
    period = world.store.periods("m1")[-1]
    assert period["result"] == "up" and period["why"].startswith("F v1 has no Mar 3 value; it stopped")
    assert world.store.frame("m1", datetime=61 * DAY).empty, "no value made from F's day-60 value"
    assert world.store.periods("f2")[-1]["result"] == "ok"


def test_a_factor_update_asks_only_for_the_bars_after_the_period_it_waits_for(tmp_path):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from modules.quant.backend.library_produce import FactorProducer

    @asynccontextmanager
    async def slot(**_):
        yield

    refreshed = []

    async def prepare(data, *, report=None, refresh_after=None):
        refreshed.append(refresh_after)
        return {"key": "k", "barsPath": "bars", "info": {}, "extrasPath": None}

    class Engine:
        def __init__(self, error):
            self.error = error

        async def run_open(self, job):
            raise self.error

    version = {"id": "v1", "meta": {"data": {}, "settings": {}, "formula": "rank($close)", "origin": {"objectName": "F"}}}

    def producer(error):
        factors = SimpleNamespace(data=SimpleNamespace(prepare=prepare), queue=SimpleNamespace(slot=slot), engine=Engine(error))
        return FactorProducer(None, factors, SimpleNamespace(version_directory=lambda identity: tmp_path / identity))
    with pytest.raises(RuntimeError):
        asyncio.run(producer(RuntimeError("stop here")).make(version, through=DAY, after=None, now=1, attempt=0))
    assert refreshed and refreshed[0] is not None, "the freeze is asked to be newer than the period this update waits for, so only the new bar is fetched"


@pytest.mark.parametrize("known_by", [None, "after the update"])
def test_a_standard_models_training_and_update_both_read_the_factors_value_of_the_same_bar(tmp_path, known_by):
    """The standard pipeline puts each library value on the bar it is about, in training and in an update alike: the day-2 value, made after day 2's
    close, is day 2's input (never day 3's, never day 1's carried over), so a published model reads its factor as it was trained."""
    from types import SimpleNamespace

    from modules.quant.backend import model_prep
    from modules.quant.backend.library_loop import DELAY
    from modules.quant.native import model_data
    rows = [(DAY, "x:A", "value", 1.0, close_of(1), close_of(1), "history"), (2 * DAY, "x:A", "value", 2.0, close_of(2) + DELAY, close_of(2) + DELAY, "scheduled")]
    import pandas as pd
    table = pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value", "availableAt", "recordedAt", "mode"])
    library = SimpleNamespace(store=SimpleNamespace(frame=lambda version_id, start=None, end=None, **_: table))
    info = {"instruments": [{"instrumentId": "x:A", "column": "A"}], "since": DAY, "until": 3 * DAY}
    moment = None if known_by is None else close_of(2) + DELAY + 60_000
    path = model_prep.library_table(library, "v1", info, tmp_path / "f.parquet", moment)
    panel = SimpleNamespace(labels=np.array([DAY, 2 * DAY, 3 * DAY], dtype="int64"), columns=["A"], shape=(3, 1))
    table, _late = model_data.library_table(panel, path, opens=np.array([close_of(0), close_of(1), close_of(2)], dtype="int64"))
    assert table[0, 0] == 1.0 and table[1, 0] == 2.0 and np.isnan(table[2, 0])


def test_an_update_someone_retries_runs_in_the_loops_own_context_not_in_their_request(tmp_path):
    """A Retry from the Agent or the page started the update inside that request: it inherited its deadline and every platform call of the
    update failed with "timeout" once the request was over."""
    import contextvars
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    request = contextvars.ContextVar("request", default=None)
    seen = []

    async def scenario():
        world.loop.home = contextvars.copy_context()  # what run() keeps when the loop starts
        request.set("the Agent's call, with its deadline")

        async def guarded(version_id, inputs):
            seen.append(request.get())
        world.loop.guarded = guarded
        world.loop.store.mark("v1", state="failed")
        world.loop.retry_now("v1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(scenario())
    assert seen == [None]


def test_a_library_value_counts_as_late_only_against_a_next_open_the_data_has(tmp_path):
    """The look-ahead check counts a value known after the next open; the data's last bar has no next open in the panel, so its values,
    known at their own close as every on-time value is, are not late (data that end at the latest close flagged every library input)."""
    from types import SimpleNamespace

    import pandas as pd

    from modules.quant.native import model_data
    opens = np.array([close_of(0), close_of(1), close_of(2)], dtype="int64")
    panel = SimpleNamespace(labels=np.array([DAY, 2 * DAY, 3 * DAY], dtype="int64"), columns=["A"], shape=(3, 1))
    on_time = pd.DataFrame({"label": [DAY, 2 * DAY, 3 * DAY], "column": ["A"] * 3, "value": [1.0, 2.0, 3.0],
                            "availableAt": [close_of(1), close_of(2), close_of(3)]})
    on_time.to_parquet(tmp_path / "on.parquet")
    assert model_data.library_table(panel, tmp_path / "on.parquet", opens=opens)[1] == 0
    late = on_time.assign(availableAt=[close_of(1), close_of(2) + 3 * DAY, close_of(3)])
    late.to_parquet(tmp_path / "late.parquet")
    assert model_data.library_table(panel, tmp_path / "late.parquet", opens=opens)[1] == 1


def test_a_version_stopped_while_its_update_runs_stays_stopped_when_the_update_ends(tmp_path):
    """Stopped while an update was in flight: the update's failure brought it back as failed, tried again every 10 minutes; one that made values
    brought it back as updating."""
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    world.store.stop("v1")
    asyncio.run(world.loop.fail("v1", "KeyError: 'x'", {}, labels=[(51 * DAY, 51 * DAY + 60_000)]))
    stopped = world.store.version("v1")
    assert stopped["state"] == "delisted" and stopped["retry_after"] is None and stopped["failure"] is None
    assert all(period["result"] != "fail" for period in world.store.periods("v1"))
    world.store.put_periods("v1", [], state="failed", retry_after=1)
    assert world.store.version("v1")["state"] == "delisted"


def test_a_version_published_before_a_change_it_cannot_be_produced_under_fails_once_and_is_not_tried_again(tmp_path):
    """An allocation published before allocations had a risk-free rate failed with "KeyError: 'risk_free'" and was retried every ten minutes (try 22):
    it fails once with what to do, and the loop leaves it until it is published again. Nothing offers a retry that can only fail again: the home row opens the
    object it was published from, and a retry asked for anyway (an AI, an old page) is refused with the failure's own words."""
    from types import SimpleNamespace

    from modules.quant.backend.allocation_producer import AllocationProducer
    from modules.quant.backend.library_produce import UpdateFailure
    from modules.quant.backend.library_summary import REPUBLISH
    version = {"id": "v1", "meta": {"info": {"since": 0}, "data": {}, "allocation": {"settings": {"frequency": "monthly"}, "pool": []}}}
    producer = AllocationProducer(None, SimpleNamespace(version_directory=lambda _identity: tmp_path / "v1"), None)
    with pytest.raises(UpdateFailure, match="publish it again") as failed:
        asyncio.run(producer.make(version, through=DAY, after=None, now=1, attempt=0))
    assert failed.value.details["code"] == REPUBLISH

    world = build(tmp_path / "world")
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    now = world.clock["now"]
    assert [item["id"] for item in world.loop.due_now(now)] == ["v1"]
    asyncio.run(world.loop.fail("v1", failed.value.message, failed.value.details, labels=[(51 * DAY, 51 * DAY + 60_000)]))
    failed_version = world.store.version("v1")
    assert failed_version["state"] == "failed" and failed_version["retry_after"] is None and failed_version["failure"]["retryAt"] is None
    assert world.loop.due_now(now + 10 * DAY) == [] and world.loop.next_due("v1") is None
    (row,), _ = library_summary.attention(world.store.summaries())
    assert row["action"] == {"id": "republish", "label": "Open the factor", "objectId": "o-Momentum"}
    with pytest.raises(ContractError, match="publish it again"):
        world.loop.retry_now("v1")
