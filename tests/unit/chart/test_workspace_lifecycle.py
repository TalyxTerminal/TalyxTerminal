"""A workspace's life in Chart: an opening that finishes after the workspace closed starts nothing (R18), and a copy of
a copy that was never saved still gives its panels their own charts (R20)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from talyx_sdk import ContractError
from talyx_sdk.process import Subscription

from modules.chart.backend.app import ChartApp
from tests.unit.chart.fake_market import BTC, FakeMarket
from tests.unit.chart.test_alerts import Notifier, series_alert


def test_an_opening_that_ends_after_the_workspace_closed_does_not_restart_its_alerts(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        app.alerts.notifier = Notifier()
        app.store.put_object("seeded", {"id": "w1"}, "w1")
        gate = asyncio.Event()

        async def settings(workspace):   # the settings read the opening waits for
            await gate.wait()
            app.defaults[workspace] = {"toast": True, "sound": True}
            return app.defaults[workspace]
        app.read_alert_defaults = settings

        app.workspace_opened("w1")
        alert = await app.create_alert("w1", series_alert(BTC))
        app.workspace_closed("w1")
        gate.set()
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert "w1" not in app.alerts.open
        assert alert["id"] not in app.alerts.running and alert["id"] not in app.alerts.starting
        assert "w1" not in app.opening
        await app.close()
    asyncio.run(main())


def test_a_copy_of_an_unsaved_copy_gives_the_panels_its_own_charts(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        app.store.put_chart("chart-a", "A", 1, {"id": "chart-a"})
        app.copy_workspace("A", "B")     # B's panels name chart-a (A was saved, B is not)
        app.copy_workspace("B", "C")     # so C's panels name chart-a too
        b = (await app.adopt("B", SimpleNamespace(chartIds=["chart-a"])))["charts"]["chart-a"]
        c = (await app.adopt("C", SimpleNamespace(chartIds=["chart-a"])))["charts"]["chart-a"]
        assert b in app.store.charts("B") and c in app.store.charts("C")
        await app.close()
    asyncio.run(main())


def test_starting_chart_listens_for_openings_before_it_lists_the_open_workspaces(tmp_path, monkeypatch):
    """Chart started while workspaces are open (after its own or the Host's restart): an opening between the list and
    the subscription is not missed."""
    from modules.chart.backend import entry
    steps = []

    async def storage():
        return {"dataDir": str(tmp_path / "data"), "userDir": str(tmp_path / "user")}

    async def subscribe(name):
        steps.append(("subscribe", name))
        changes = Subscription(SimpleNamespace(subscriptions={}), name, {}, None, None)
        if name == "platform.workspace.changes":
            changes.queue.put_nowait(("item", {"kind": "opened", "workspaceId": "later"}, None))
        return changes

    async def open_workspaces():
        steps.append(("list",))
        return ["desk"]

    async def main():
        ctx = SimpleNamespace(platform=SimpleNamespace(storage=storage, open_workspaces=open_workspaces),
                              subscribe=subscribe)
        await entry.start(ctx)
        await asyncio.sleep(0)
        await entry.app.close()

    monkeypatch.setattr(ChartApp, "warm", lambda self: None)
    monkeypatch.setattr(ChartApp, "workspace_opened", lambda self, ws: steps.append(("opened", ws)))
    monkeypatch.setattr(entry, "app", None)
    asyncio.run(main())
    assert steps[:3] == [("subscribe", "platform.workspace.changes"), ("list",), ("opened", "desk")]
    assert ("opened", "later") in steps


def test_chart_close_joins_the_watcher_before_releasing_its_subscription(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        changes = SimpleNamespace(name="platform.workspace.changes", close=AsyncMock())
        listening, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def follow(stream):
            listening.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
                await release.wait()

        app.watch_platform(changes, follow)
        await listening.wait()
        closing = asyncio.create_task(app.close())
        try:
            await asyncio.wait_for(cancelled.wait(), 2)
            assert not closing.done()
            changes.close.assert_not_awaited()
        finally:
            release.set()
            await closing
        changes.close.assert_awaited_once()
    asyncio.run(main())


@pytest.mark.parametrize(("code", "message", "reported"), [
    ("unavailable", "TALYX is closing", False),
    ("unavailable", "workspace service disconnected", True),
    ("permission_denied", "TALYX is closing", True),
])
def test_chart_reports_unexpected_workspace_stream_errors(tmp_path, monkeypatch, caplog, code, message, reported):
    from modules.chart.backend import entry

    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        monkeypatch.setattr(entry, "app", app)
        changes = Subscription(SimpleNamespace(subscriptions={}), "platform.workspace.changes", {}, None, None)
        error = ContractError(code, message)
        changes.queue.put_nowait(("end", error, None))
        app.watch_platform(changes, entry._follow_workspaces)
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # task completion callback
        assert bool([r for r in caplog.records if r.exc_info and r.exc_info[1] is error]) is reported
        await app.close()
        assert changes.closed
    asyncio.run(main())

def test_save_conflict_preserves_all_previously_saved_charts(tmp_path):
    import pytest
    from talyx_sdk import ContractError

    from modules.chart.backend.wire import ChartCreate, ChartSave
    from tests.unit.chart.test_chart_flow import commit

    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        charts = [await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"])) for _ in range(3)]
        refs = [{"chartId": item["chartId"], "revision": 1} for item in charts]
        await app.save("w1", ChartSave(charts=refs))
        for item in refs[:2]:
            await app.commit("w1", commit(item["chartId"], 1, {"type": "display.update", "patch": {"log": True}}))
        with pytest.raises(ContractError, match="changed meanwhile"):
            await app.save("w1", ChartSave(charts=[{**refs[0], "revision": 2}, refs[1]]))
        for item in refs:
            assert app.store.chart(item["chartId"])["savedRevision"] == 1
        late = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"]))
        await app.save("w1", ChartSave(charts=[{**item, "revision": 2} for item in refs[:2]]))
        assert app.store.chart(late["chartId"]) is not None
        assert app.store.chart(refs[-1]["chartId"]) is None
        app.workspace_closed("w1")
        assert app.store.chart(late["chartId"]) is None
        for item in refs[:2]:
            assert app.charts.entry("w1", item["chartId"]).revision == 2
        await app.close()
    asyncio.run(main())
