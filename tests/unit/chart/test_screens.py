"""Screens: the latest closed bar of each member against every condition, over the window
charts and alerts use (done criterion 5: the same value on the chart and in the screen)."""
import asyncio

import pytest
from talyx_sdk import ContractError

from modules.chart.backend.app import ChartApp
from modules.chart.backend.screens import legend_name
from modules.chart.backend.wire import ChartCreate, ScreenId, ScreenSave
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket
from tests.unit.chart.test_chart_flow import Reader, commit
from tests.unit.chart.test_pine_engine import bars


def history(seed, last_close):
    rows = bars(400, seed)
    rows[-1] = {**rows[-1], "close": last_close, "high": max(rows[-1]["high"], last_close),
                "low": min(rows[-1]["low"], last_close)}
    return rows


async def finished(app, screen_id):
    for _ in range(3000):
        screen = app.screens.get("w1", screen_id)
        if screen["lastRun"]["state"] != "running":
            return screen
        await asyncio.sleep(0.02)
    raise AssertionError("the run never finished")


def test_a_screen_judges_each_members_last_closed_bar_with_the_charts_values(tmp_path):
    async def main():
        market = FakeMarket()
        market.histories = {BTC["instrumentId"]: history(3, 1000.0), ETH["instrumentId"]: history(4, 1.0)}
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        spec = {"name": "Strong", "scope": {"kind": "instruments", "instruments": [BTC, ETH]}, "timeframe": "1m",
                "conditions": [{"left": {"kind": "price"}, "op": "greater", "right": {"kind": "value", "value": 50}},
                               {"left": {"kind": "indicator", "definition": {"source": "builtin", "id": "rsi"},
                                         "output": "rsi"}, "op": "greater", "right": {"kind": "value", "value": 0}}]}
        saved = await app.save_screen("w1", ScreenSave.model_validate({"spec": spec}))
        assert saved["revision"] == 1 and app.snapshot("w1")["screens"] == [saved]
        running = await app.run_screen("w1", ScreenId(screenId=saved["id"]))
        assert running["lastRun"]["state"] == "running" and running["lastRun"]["total"] == 2
        done = await finished(app, saved["id"])
        rows = {row["instrument"]["instrumentId"]: row for row in done["lastRun"]["rows"]}
        assert done["lastRun"]["state"] == "completed" and done["lastRun"]["done"] == 2
        assert rows[BTC["instrumentId"]]["matched"] and not rows[ETH["instrumentId"]]["matched"]
        btc = rows[BTC["instrumentId"]]
        assert btc["barTime"] == market.histories[BTC["instrumentId"]][-1]["time"]
        assert btc["values"]["Close"] == 1000.0

        # the chart on the same window shows the same RSI on that bar
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        await app.commit("w1", commit(chart_id, 1, {"type": "indicator.add", "value": {
            "id": "rsi", "definition": {"source": "builtin", "id": "rsi"}, "paneId": "pane-rsi"}}))
        reader = Reader(await app.chart_stream("w1", chart_id))
        (await market.session(BTC["instrumentId"])).replace(market.histories[BTC["instrumentId"]])
        replace = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi" and item["op"] == "replace")
        assert replace["values"]["rsi"][-1] == btc["values"]["RSI 14 · rsi"]  # named as the legend names it

        with pytest.raises(ContractError) as stale:
            await app.save_screen("w1", ScreenSave.model_validate({"screenId": saved["id"], "revision": 7, "spec": spec}))
        assert stale.value.code == "conflict"
        await app.close()
    asyncio.run(main())


def test_a_screen_run_can_be_cancelled_and_crossing_looks_at_the_bar_before(tmp_path):
    async def main():
        market = FakeMarket()
        rows = history(5, 200.0)
        rows[-2] = {**rows[-2], "close": 50.0}
        market.histories = {BTC["instrumentId"]: rows}
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        saved = await app.save_screen("w1", ScreenSave.model_validate({"spec": {
            "name": "Cross", "scope": {"kind": "instruments", "instruments": [BTC]}, "timeframe": "1m",
            "conditions": [{"left": {"kind": "price"}, "op": "crossing_up", "right": {"kind": "value", "value": 100}}]}}))
        await app.run_screen("w1", ScreenId(screenId=saved["id"]))
        done = await finished(app, saved["id"])
        assert done["lastRun"]["rows"][0]["matched"]
        market.read_gate = asyncio.Event()
        await app.run_screen("w1", ScreenId(screenId=saved["id"]))
        cancelled = await app.cancel_screen("w1", ScreenId(screenId=saved["id"]))
        assert cancelled["lastRun"]["state"] == "cancelled"
        with pytest.raises(ContractError):
            await app.save_screen("w1", ScreenSave.model_validate({"spec": {
                "name": "Bad", "scope": {"kind": "instruments", "instruments": [BTC]}, "timeframe": "1m",
                "conditions": [{"left": {"kind": "drawing", "chartId": "c", "drawingId": "d"}, "op": "greater",
                                "right": {"kind": "value", "value": 1}}]}}))
        await app.close()
    asyncio.run(main())


def test_a_screen_column_names_an_indicator_as_the_legend_does():
    inputs = [{"id": "length", "type": "int", "default": 20}, {"id": "mult", "type": "float", "default": 2.0},
              {"id": "src", "type": "source", "default": "close"}, {"id": "fill", "type": "bool", "default": True}]
    assert legend_name({"shortTitle": "BB", "inputs": inputs}, {"length": 30}) == "BB 30 2"
