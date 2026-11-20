"""Chart's health item on the workspace stream: the indicator processes and the database's writes,
in the snapshot and again whenever either changes; a restart starts the processes anew."""
import asyncio
import sqlite3

import pytest

from modules.chart.backend.app import ChartApp
from tests.unit.chart.fake_market import FakeMarket
from tests.unit.chart.test_chart_flow import Reader


def test_a_failed_write_shows_until_the_next_one_succeeds_and_restart_starts_the_processes(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        stream = Reader(app.workspace_stream("w1"))
        snapshot = await stream.until(lambda item: True)
        assert snapshot["health"] == {"pine": {"state": "down", "reason": "not running"},
                                      "alertStore": {"state": "ok", "reason": None}}

        app.store.db.execute("CREATE TRIGGER full BEFORE INSERT ON fired BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        event = {"id": "fi-1", "alertId": "al-1", "at": 1}
        with pytest.raises(sqlite3.Error):
            app.store.add_fired("w1", event, "Price crossed 100")
        failing = await stream.until(lambda item: item["type"] == "health")
        assert failing["alertStore"] == {"state": "failing", "reason": "disk full"}
        app.store.db.execute("DROP TRIGGER full")
        app.store.add_fired("w1", event, "Price crossed 100")
        recovered = await stream.until(lambda item: item["type"] == "health")
        assert recovered["alertStore"] == {"state": "ok", "reason": None}

        app.engine.restart_pool()
        assert (await stream.until(lambda item: item["type"] == "health"))["pine"]["state"] == "restarting"
        await stream.until(lambda item: item["type"] == "health" and item["pine"]["state"] == "ok", timeout=120)
        await app.close()
    asyncio.run(main())
