"""Bar replay: the window up to a chosen bar, then bar by bar; the
replay's own indicator instances step to what a whole computation gives; live bars stay off the
stream until the replay stops, then the live state comes back."""
import asyncio

from modules.chart.backend.app import ChartApp
from modules.chart.backend.wire import ChartCreate, ChartTarget, ReplayStart, ReplayStep
from tests.unit.chart.fake_market import BTC, FakeMarket
from tests.unit.chart.test_chart_flow import Reader, commit
from tests.unit.chart.test_pine_engine import bars


def test_replay_steps_bars_and_its_own_indicators_and_stopping_brings_the_live_chart_back(tmp_path):
    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        await reader.until(lambda item: item["type"] == "document")
        rows = bars(1100, 5)
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:1000])
        live_bars = await reader.until(lambda item: item["type"] == "bars")
        await app.commit("w1", commit(chart_id, 1, {"type": "indicator.add", "value": {
            "id": "rsi", "definition": {"source": "builtin", "id": "rsi"}, "paneId": "pane-rsi"}}))
        live_rsi = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi")
        assert live_rsi["replay"] is False and len(live_rsi["time"]) == 1000

        await app.start_replay("w1", ReplayStart(chartId=chart_id, at=rows[799]["time"]))
        replay_bars = await reader.until(lambda item: item["type"] == "bars")
        assert replay_bars["replay"] and replay_bars["op"] == "replace" and replay_bars["time"] == live_bars["time"][:800]
        assert await reader.until(lambda item: item["type"] == "replay") == {
            "type": "replay", "active": True, "at": rows[799]["time"], "end": False}
        replay_rsi = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi")
        assert replay_rsi["replay"] and replay_rsi["time"] == live_bars["time"][:800]
        assert replay_rsi["values"]["rsi"] == live_rsi["values"]["rsi"][:800]

        # Live bars keep coming from Market, but not onto the stream.
        session.append([rows[1000]])
        session.update(rows[1001])
        await app.step_replay("w1", ReplayStep(chartId=chart_id, bars=3))
        stepped = await reader.until(lambda item: item["type"] == "bars")
        assert stepped["replay"] and stepped["op"] == "append" and stepped["time"] == live_bars["time"][800:803]
        assert await reader.until(lambda item: item["type"] == "replay") == {
            "type": "replay", "active": True, "at": rows[802]["time"], "end": False}
        appended = []
        while len(appended) < 3:
            item = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi")
            assert item["replay"] and item["op"] == "append"
            appended += item["values"]["rsi"]
        assert appended == live_rsi["values"]["rsi"][800:803]  # stepped equals the whole computation
        assert not [item for item in reader.items if item["type"] in ("bars", "update", "indicator")
                    and not item.get("replay", False) and reader.items.index(item) > reader.items.index(replay_bars)]

        await app.step_replay("w1", ReplayStep(chartId=chart_id, bars=1000))  # past the copied window's end
        assert await reader.until(lambda item: item["type"] == "replay") == {
            "type": "replay", "active": True, "at": rows[999]["time"], "end": True}
        await app.stop_replay("w1", ChartTarget(chartId=chart_id))
        assert await reader.until(lambda item: item["type"] == "replay") == {
            "type": "replay", "active": False, "at": None, "end": False}
        back = await reader.until(lambda item: item["type"] == "bars" and not item["replay"])
        assert not back["replay"] and back["time"][-1] == rows[1000]["time"]
        assert (await reader.until(lambda item: item["type"] == "update"))["bar"]["time"] == rows[1001]["time"]
        rsi = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi")
        assert not rsi["replay"] and rsi["op"] == "replace"
        if rsi["time"][-1] != rows[1000]["time"]:  # the live instance was still computing the appended bar
            later = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi" and item["op"] == "append")
            assert not later["replay"] and later["time"] == [rows[1000]["time"]]
        await app.close()
    asyncio.run(main())


def test_heikin_ashi_replay_projects_bar_by_bar_like_the_whole_window(tmp_path):
    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        await reader.until(lambda item: item["type"] == "document")
        session = await market.session(BTC["instrumentId"])
        session.replace(bars(600, 6))
        await reader.until(lambda item: item["type"] == "bars")
        await app.commit("w1", commit(chart_id, 1, {"type": "chart_type.set", "chartType": "heikin_ashi"}))
        whole = await reader.until(lambda item: item["type"] == "bars" and item["op"] == "replace")
        await app.start_replay("w1", ReplayStart(chartId=chart_id, at=whole["time"][399]))
        replay = await reader.until(lambda item: item["type"] == "bars" and item["replay"])
        assert replay["time"] == whole["time"][:400]
        columns = {name: list(replay[name]) for name in ("time", "open", "high", "low", "close")}
        for _ in range(20):
            await app.step_replay("w1", ReplayStep(chartId=chart_id, bars=1))
            splice = await reader.until(lambda item: item["type"] == "bars" and item["replay"] and item["op"] == "splice")
            for name in columns:
                columns[name][splice["start"]:] = splice[name]
        for name in columns:
            assert columns[name] == whole[name][:420], name
        # Another chart type ends the replay.
        await app.commit("w1", commit(chart_id, 2, {"type": "chart_type.set", "chartType": "candles"}))
        assert await reader.until(lambda item: item["type"] == "replay" and not item["active"]) == {
            "type": "replay", "active": False, "at": None, "end": False}
        assert not (await reader.until(lambda item: item["type"] == "bars"))["replay"]
        await app.close()
    asyncio.run(main())
