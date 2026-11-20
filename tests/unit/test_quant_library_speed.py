"""Speed as rules: the library list of 30 items answers within a second, a day of values within half a second, and neither the list nor
writing a publication holds Host's main loop (a ticker on the loop keeps ticking meanwhile)."""
import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
from fastapi import APIRouter

from tests.unit.quant_library_fakes import DAY, build, close_of, factor_spec

NAMES, DAYS = 500, 2000
STALL = 0.15  # the longest the loop may go without running another task


class Ticker:
    """Measures the longest gap between two turns of the event loop while some work runs."""

    def __init__(self):
        self.longest, self.running = 0.0, True

    async def tick(self):
        last = time.perf_counter()
        while self.running:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            self.longest = max(self.longest, now - last - 0.005)
            last = now

    async def around(self, work):
        ticking = asyncio.create_task(self.tick())
        await asyncio.sleep(0.01)
        started = time.perf_counter()
        try:
            result = await work
        finally:
            took = time.perf_counter() - started
            self.running = False
            await ticking
        return result, took


def route(services, path, method="GET"):
    from modules.quant.backend.library_routes import mount_library
    router = APIRouter()
    mount_library(router, services)
    return next(item.endpoint for item in router.routes if item.path == path and method in item.methods)


def services_of(world):
    return SimpleNamespace(library=world.library, publisher=None, lists=None, common=None, notifications=None, history=None, source=None, home=None)


def wide(version_id, name, days=DAYS, names=NAMES, seed=1):
    rng = np.random.default_rng(seed)
    labels = np.repeat(np.arange(1, days + 1, dtype="int64") * DAY, names)
    table = pd.DataFrame({"instrument": np.tile([f"i:{index:04d}" for index in range(names)], days), "field": "value", "datetime": labels,
                          "value": rng.normal(size=days * names), "availableAt": labels + 21 * 3_600_000, "recordedAt": labels + 21 * 3_600_000, "mode": "history"})
    spec = factor_spec(name, version_id, days)
    return {**spec, "rows": table, "meta": {**spec["meta"], "instrumentCount": names}}


def test_the_list_of_thirty_items_answers_within_a_second_without_holding_the_loop(tmp_path):
    world = build(tmp_path)
    for index in range(30):
        world.store.commit_versions([factor_spec(f"Factor {index:02d}", f"v{index}", 250)])
        world.store.commit_versions([factor_spec(f"Factor {index:02d}", f"w{index}", 250)])
    listing = route(services_of(world), "/library")

    async def scenario():
        await listing()  # the first read opens the catalog
        return await Ticker().around(listing()), Ticker()
    (listed, took), _ = asyncio.run(scenario())
    assert len(listed["items"]) == 30 and took <= 1.0

    async def held():
        ticker = Ticker()
        await ticker.around(listing())
        return ticker.longest
    assert asyncio.run(held()) < STALL


def test_a_day_of_values_of_a_wide_factor_answers_within_half_a_second(tmp_path):
    world = build(tmp_path)
    world.store.commit_versions([wide("big", "Wide")])
    day = route(services_of(world), "/library/versions/{version_id}/values")
    query = SimpleNamespace(date=None, order="top", q=None, page=0, field=None)

    async def scenario():
        timings = []
        for date in (None, 1000 * DAY, 3 * DAY):
            started = time.perf_counter()
            page = await day("big", SimpleNamespace(**{**vars(query), "date": date}))
            timings.append(time.perf_counter() - started)
            assert page["have"] == NAMES and len(page["rows"]) > 0
        return timings
    assert max(asyncio.run(scenario())) <= 0.5


def test_writing_a_publication_does_not_hold_the_loop(tmp_path, monkeypatch):
    from modules.quant.backend import library_publish
    from modules.quant.backend.library_publish import Publisher
    world = build(tmp_path)
    labels = np.arange(1, DAYS + 1, dtype="int64") * DAY
    columns = [f"c{index:04d}" for index in range(NAMES)]
    values = pd.DataFrame({"datetime": np.repeat(labels, NAMES), "instrument": np.tile(columns, DAYS), "value": np.random.default_rng(2).normal(size=DAYS * NAMES)})
    info = {"timeframe": "1d", "timezone": "UTC", "calendarId": "XNYS", "sessionScope": "regular", "annualization": 252, "since": DAY, "until": DAYS * DAY,
            "instruments": [{"column": column, "instrumentId": "i:" + column} for column in columns]}

    async def boundaries(*args):
        return None, []
    monkeypatch.setattr(library_publish.research_calendar, "boundaries", boundaries)

    async def date_window(_ctx, _calendar, _timeframe, start, end):
        return start, end
    monkeypatch.setattr(library_publish.research_calendar, "date_window", date_window)
    monkeypatch.setattr(library_publish, "edge_times", lambda labels, *args: {label: close_of(label // DAY) for label in labels})
    records = {"pub": {"publish": {"name": "Wide", "auto": True}}}
    runs = SimpleNamespace(read=lambda identity: records[identity], update=lambda identity, **fields: records[identity].update(fields),
                           store=SimpleNamespace(workspace=lambda: "ws"))
    factors = SimpleNamespace(update=lambda identity, **fields: None, read=lambda identity: {})
    publisher = Publisher(world.ctx, world.records, world.store, runs, world.library, factors)
    source = {"id": "r1", "objectId": "o1", "name": "Wide", "number": 4, "mode": "formula", "formula": "rank($close)", "numbers": {"rankIc": 0.031, "t": 3.2},
              "settings": {"run": {"holding": 5, "delay": 1}, "data": {"symbols": []}, "params": {}}}
    ready = {"info": info, "extrasPath": None, "directory": str(tmp_path / "frozen")}

    async def scenario():
        ticker = Ticker()
        created, took = await ticker.around(publisher.commit_factor("pub", source, ready, values, {"checked": 3}))
        return created, took, ticker.longest
    created, took, longest = asyncio.run(scenario())
    version = world.library.read_version(created[0]["versionId"])
    assert version["rows"] == NAMES * DAYS and version["published"]["value"] == 0.031, "the published figure is the run's own number"
    assert longest < STALL, f"the loop stalled {longest:.3f}s while {took:.2f}s of writing ran"
