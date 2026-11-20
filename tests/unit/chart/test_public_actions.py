"""Chart's public actions: the same backend functions the panel calls,
bounded reads, one error format, AI on the same indicators as the user."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError
from talyx_sdk.process import Caller

from modules.chart.backend import actions
from modules.chart.backend.actions import iso
from modules.chart.backend.app import ChartApp
from modules.chart.backend.document import drawing_group
from modules.chart.backend.wire import ChartCreate
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket
from tests.unit.chart.test_chart_flow import Reader
from tests.unit.chart.test_pine_engine import bars

ROOT = Path(__file__).resolve().parents[3]
AI = SimpleNamespace(caller=Caller("platform", "w1", "ai", {"client": "test"}), workspace_id="w1")
USER_SMA = '''"""@pyne"""
from pynecore.lib import close, input, plot, script, ta


@script.indicator("My SMA", overlay=True)
def main(period: int = input.int(5, "Period")):
    plot(ta.sma(close, period), "sma")
'''


class QuotedMarket(FakeMarket):
    """FakeMarket with Market's quotes service."""

    async def quotes(self, value):
        return {"receivedAt": "0", "items": [{"instrumentId": item, "problem": None, "quote": {
            "price": "101.5", "last": "101.5", "bid": None, "ask": None, "mark": None, "index": None,
            "change": "1.5", "changePct": "1.5", "changeBasis": "rolling_24h", "asOf": "2023-11-14T22:13:20Z",
            "fieldAsOf": {}, "delayed": False}} for item in value["symbols"]]}


def opened(tmp_path):
    market = QuotedMarket()
    (tmp_path / "indicators").mkdir(exist_ok=True)
    (tmp_path / "indicators" / "my_sma.py").write_text(USER_SMA, encoding="utf-8")
    return market, ChartApp(market, tmp_path / "data", tmp_path / "indicators")


def call(app, name, value, caller=AI):
    return actions.handlers(app)[name](value, caller)


async def raises(code, awaitable):
    with pytest.raises(ContractError) as error:
        await awaitable
    assert error.value.code == code, error.value.message
    return error.value


def test_a_chart_created_with_a_lower_pane_indicator_gets_its_pane(tmp_path):
    async def main():
        _market, app = opened(tmp_path)
        created = await call(app, "write_chart", {"operation": {"op": "create", "symbol": "okx:BTC/USDT", "timeframe": "1d",
                             "indicators": [{"name": "sma", "params": {"period": 20}}, {"name": "rsi", "params": {"period": 14}}]}})
        document = app.charts.entry("w1", created["chartId"]).document
        assert [item.paneId for item in document.indicators][0] == "price"
        assert [pane.id for pane in document.panes] == ["price", document.indicators[1].paneId]
    asyncio.run(main())


def test_ai_reads_and_changes_a_chart_at_its_revision(tmp_path):
    async def main():
        _market, app = opened(tmp_path)
        created = await call(app, "write_chart", {"operation":{"op":"create", "symbol":"okx:BTC/USDT",
                             "timeframe":"1m", "indicators":[{"name":"volume"}]}})
        chart_id = created["chartId"]
        volume_id = app.charts.entry("w1", chart_id).document.indicators[0].id
        assert created["openTarget"] == {"module":"chart", "panelType":"main", "objectId":"chart:" + chart_id}
        listed = await call(app, "read_chart", {"view": "list"})
        assert [(item["chartId"], item["symbol"], item["revision"], item["saved"]) for item in listed["charts"]] == [
            (chart_id, "okx:BTC/USDT", 1, False)]
        edited = await call(app, "write_chart", {"operation": {"op": "edit", "chartId": chart_id, "baseRevision": 1, "changes": [
            {"kind": "indicator", "name": "rsi", "params": {"period": 7}},
            {"kind": "indicator", "id": "e", "name": "ema"},
            {"kind": "drawing", "tool": "hline", "points": [{"time": "2023-11-14T22:13:20Z", "price": 100.5}]},
            {"kind": "comparison", "symbol": "okx:ETH/USDT"}]}})
        assert edited["revision"] == 2 and edited["added"]["indicators"][1] == "e"
        rsi_id, drawing_id = edited["added"]["indicators"][0], edited["added"]["drawings"][0]
        one = await call(app, "read_chart", {"view": "one", "chartId": chart_id})
        assert {(item["id"], item["name"], item["paneId"]) for item in one["indicators"]} == {
            (volume_id, "volume", "price"), (rsi_id, "rsi", f"pane-{rsi_id}"), ("e", "ema", "price")}  # an oscillator gets its own pane
        assert one["drawings"] == [{"id": drawing_id, "tool": "hline", "paneId": "price",
                                    "points": [{"time": "2023-11-14T22:13:20.000Z", "price": 100.5}]}]
        assert one["comparisons"][0]["symbol"] == "okx:ETH/USDT"
        assert app.charts.entry("w1", chart_id).undo  # on the user's undo stack, like the panel's own changes

        stale = await raises("conflict", call(app, "write_chart", {"operation": {
            "op": "remove", "chartId": chart_id, "baseRevision": 1, "drawings": [drawing_id]}}))
        assert stale.details == {"revision": 2}
        await raises("not_found", call(app, "write_chart", {"operation": {
            "op": "edit", "chartId": chart_id, "baseRevision": 2, "changes": [{"kind": "indicator", "name": "nope"}]}}))
        await raises("invalid_input", call(app, "write_chart", {"operation": {
            "op": "edit", "chartId": chart_id, "baseRevision": 2, "changes": [{"kind": "drawing", "tool": "trendline",
                                                                              "points": [{"time": "2024-01-01", "price": 1}]}]}}))
        # AI puts the user's own indicator on a chart, as the user does
        added = await call(app, "write_chart", {"operation": {"op": "edit", "chartId": chart_id, "baseRevision": 2, "changes": [
            {"kind": "indicator", "name": "my_sma.py"}]}})
        removed = await call(app, "write_chart", {"operation": {"op": "remove", "chartId": chart_id, "baseRevision": 3,
                                                                "drawings": [drawing_id], "indicators": ["e"]}})
        assert added["revision"] == 3 and removed["revision"] == 4
        document = app.charts.entry("w1", chart_id).document
        assert not document.drawings.get(drawing_group(document)) and [item.id for item in document.indicators] == [
            volume_id, rsi_id, added["added"]["indicators"][0]]
        assert document.indicators[-1].definition.model_dump() == {"source": "user", "id": "my_sma.py"}
        await raises("invalid_input", call(app, "read_chart", {"view": "list"},
                                           SimpleNamespace(caller=Caller("platform", None, "user"), workspace_id=None)))
        await app.close()
    asyncio.run(main())


def test_read_data_gives_the_values_the_chart_shows_in_bounded_pages(tmp_path):
    async def main():
        market, app = opened(tmp_path)
        rows = bars(400, 7)
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        await call(app, "write_chart", {"operation": {"op": "edit", "chartId": chart_id, "baseRevision": 1, "changes": [
            {"kind": "indicator", "id": "rsi", "name": "rsi"}]}})
        reader = Reader(await app.chart_stream("w1", chart_id))
        (await market.session(BTC["instrumentId"])).replace(rows)
        shown = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi"
                                   and item["op"] == "replace")

        page = await call(app, "read_data", {"chartId": chart_id, "limit": 5})
        assert page["columns"] == ["time", "open", "high", "low", "close", "volume", "volume.volume", "rsi.rsi"]
        assert [row[0] for row in page["rows"]] == [iso(row["time"]) for row in rows[-5:]]
        assert [row[-1] for row in page["rows"]] == shown["values"]["rsi"][-5:]
        assert [item["outputs"] for item in page["indicators"]] == [{"volume.volume": "volume"}, {"rsi.rsi": "rsi"}]
        assert page["window"]["bars"] == 400
        older = await call(app, "read_data", {"chartId": chart_id, "limit": 5, "cursor": page["nextCursor"],
                                              "columns": ["close"]})
        assert older["columns"] == ["time", "close"] and older["rows"][-1] == [iso(rows[-6]["time"]), rows[-6]["close"]]

        # a symbol with indicators and no chart: the same instance's values; the user's indicators too
        free = await call(app, "read_data", {"symbol": "okx:BTC/USDT", "timeframe": "1m", "limit": 3,
                                             "indicators": [{"name": "rsi"}]})
        assert [row[-1] for row in free["rows"]] == shown["values"]["rsi"][-3:]
        own = await call(app, "read_data", {"symbol": "okx:BTC/USDT", "timeframe": "1m", "limit": 3,
                                            "indicators": [{"name": "my_sma.py"}]})
        assert own["indicators"][0]["source"] == "user" and "error" not in own["indicators"][0]
        assert own["columns"][-1] == "my_sma.py1.sma"
        assert own["rows"][-1][-1] == pytest.approx(sum(row["close"] for row in rows[-5:]) / 5)
        large = await raises("too_large", call(app, "read_data", {"chartId": chart_id, "limit": 500}))
        assert 0 < large.details["fits"] < 500
        fitting = await call(app, "read_data", {"chartId": chart_id, "limit": large.details["fits"]})
        assert len(json.dumps(fitting, ensure_ascii=False)) <= 18_000
        await app.close()
    asyncio.run(main())


def test_alerts_watchlists_and_screens_through_their_actions(tmp_path):
    async def main():
        market, app = opened(tmp_path)
        market.histories = {BTC["instrumentId"]: bars(300, 3), ETH["instrumentId"]: bars(300, 4)}
        created = await call(app, "write_watchlist", {"operation": {"op": "create", "name": "Majors", "sections": [
            {"name": "Crypto", "symbols": ["okx:BTC/USDT"]}]}})
        watchlist_id = created["watchlist"]["id"]
        updated = await call(app, "write_watchlist", {"operation": {"op": "update", "watchlistId": watchlist_id,
                                                                    "revision": 1, "add": [{"symbol": "okx:ETH/USDT"}]}})
        assert updated["watchlist"]["sections"] == [{"name": "Crypto", "members": 2}]
        one = await call(app, "read_watchlist", {"view": "one", "watchlistId": watchlist_id})
        assert [(row["symbol"], row["quote"]["price"]) for row in one["members"]] == [
            ("okx:BTC/USDT", "101.5"), ("okx:ETH/USDT", "101.5")]
        await raises("conflict", call(app, "write_watchlist", {"operation": {
            "op": "update", "watchlistId": watchlist_id, "revision": 1, "remove": ["okx:ETH/USDT"]}}))

        alert = (await call(app, "write_alert", {"operation": {"op": "create", "target": {
            "symbol": "okx:BTC/USDT", "timeframe": "1m"}, "conditions": [
            {"left": {"kind": "indicator", "name": "rsi", "output": "rsi"}, "op": "greater",
             "right": {"kind": "value", "value": 70}}], "expiresAt": "2030-01-01"}}))["alert"]
        assert alert["target"] == {"symbol": "okx:BTC/USDT", "timeframe": "1m"}
        assert alert["conditions"][0]["left"] == {"kind": "indicator", "name": "rsi", "source": "builtin",
                                                  "params": {}, "output": "rsi"}
        assert alert["expiresAt"] == "2030-01-01T00:00:00.000Z"
        changed = (await call(app, "write_alert", {"operation": {"op": "update", "alertId": alert["id"],
                               "revision": alert["revision"], "name": "Watch", "channels": {"mobile": True}}}))["alert"]
        assert changed["target"] == alert["target"] and changed["conditions"] == alert["conditions"]
        assert changed["channels"] == {**alert["channels"], "mobile": True}
        paused = (await call(app, "write_alert", {"operation": {"op": "pause", "alertId": alert["id"], "revision": changed["revision"]}}))["alert"]
        assert paused["state"] == "paused"
        listed = await call(app, "read_alert", {"view": "list", "state": "paused"})
        assert [item["id"] for item in listed["alerts"]] == [alert["id"]]
        own = (await call(app, "write_alert", {"operation": {"op": "create", "target": {
            "watchlistId": watchlist_id, "timeframe": "1m"}, "conditions": [
            {"left": {"kind": "indicator", "name": "my_sma.py", "output": "sma"}, "op": "greater",
             "right": {"kind": "price"}}]}}))["alert"]
        assert own["conditions"][0]["left"]["source"] == "user"
        await call(app, "write_alert", {"operation": {"op": "delete", "alertId": own["id"]}})
        await call(app, "write_alert", {"operation": {"op": "delete", "alertId": alert["id"]}})
        assert (await call(app, "read_alert", {"view": "log"}))["events"] == []

        saved = (await call(app, "write_screen", {"operation": {"op": "save", "name": "Above my SMA", "scope": {
            "watchlistId": watchlist_id}, "timeframe": "1m", "conditions": [
            {"left": {"kind": "price"}, "op": "greater",
             "right": {"kind": "indicator", "name": "my_sma.py", "output": "sma"}}]}}))["screen"]
        assert saved["conditions"][0]["right"]["source"] == "user"
        ran = await call(app, "write_screen", {"operation": {"op": "run", "screenId": saved["id"]}})
        assert ran["screen"]["lastRun"]["state"] == "completed" and ran["screen"]["lastRun"]["total"] == 2
        rows = await call(app, "read_screen", {"view": "one", "screenId": saved["id"]})
        assert {row["symbol"] for row in rows["rows"]} == {"okx:BTC/USDT", "okx:ETH/USDT"}
        assert sorted(ran["matched"]) == sorted(row["symbol"] for row in rows["rows"] if row["matched"])

        # cancelling the run's platform task cancels the run
        market.read_gate = asyncio.Event()
        task = asyncio.ensure_future(call(app, "write_screen", {"operation": {"op": "run", "screenId": saved["id"]}}))
        while not app.screens.runs:  # the run started and waits on Market's read
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.screens.get("w1", saved["id"])["lastRun"]["state"] == "cancelled"
        await app.close()
    asyncio.run(main())


def test_tool_definitions_stay_small():
    """What AI reads of each action (its contract entry) stays small."""
    contract = json.loads((ROOT / "modules/chart/public/contract.json").read_text(encoding="utf-8"))
    assert set(contract["actions"]) == {"read_chart", "read_data", "write_chart", "read_indicator", "read_alert",
                                        "write_alert", "read_watchlist", "write_watchlist", "read_screen",
                                        "write_screen", "restart_indicators", "write_indicator", "backfill_chart",
                                        "export_chart", "replay_chart", "write_template"}
    assert all(len(json.dumps(spec, ensure_ascii=False)) <= 9_000 for spec in contract["actions"].values())


class Developer(QuotedMarket):
    """QuotedMarket with the Developer document calls write_indicator makes, writing straight to disk."""

    def __init__(self):
        super().__init__()
        self.documents, self.calls = {}, []

    async def call(self, name, value, *, workspace=None):
        self.calls.append(name)
        if name == "developer.projects.open":
            self.root = Path(value["directory"]).parent  # Developer can return a registered enclosing project.
            return {"projectId": "p1", "rootUri": self.root.as_uri()}
        if name == "developer.documents.open":
            self.documents["d1"] = Path.from_uri(value["source"]["uri"])
            return {"documentId": "d1", "version": 1, "dirty": False, "conflict": False}
        if name == "developer.documents.edit":
            self.edited = value["content"]
            return {"documentId": "d1", "version": 2}
        if name == "developer.documents.save":
            self.documents["d1"].write_text(self.edited, encoding="utf-8")
            return {"status": "saved", "message": None}
        return await super().call(name, value, workspace=workspace)


def test_ai_writes_an_indicator_and_gets_its_check(tmp_path):
    async def main():
        _market, app = opened(tmp_path)
        app.ctx = Developer()
        created = await call(app, "write_indicator", {"operation": {"op": "create", "name": "fast.py",
                                                                    "content": USER_SMA.replace("My SMA", "Fast")}})
        assert created["name"] == "fast.py" and created["check"]["error"] is None
        assert created["check"]["title"] == "Fast" and created["check"]["inputs"]
        broken = USER_SMA.replace("plot(ta.sma(close, period), \"sma\")", "plot(ta.sma(close, period), \"sma\"")
        updated = await call(app, "write_indicator", {"operation": {"op": "update", "name": "fast.py", "content": broken}})
        assert updated["check"]["error"] and updated["check"]["line"] == 7
        one = await call(app, "read_indicator", {"view": "one", "name": "my_sma.py"})   # a user's, without source
        assert one["source"] == "user" and one["uri"] == (tmp_path / "indicators" / "my_sma.py").as_uri()
        await raises("not_found", call(app, "write_indicator", {"operation": {"op": "update", "name": "none.py",
                                                                              "content": USER_SMA}}))
        await call(app, "write_indicator", {"operation": {"op": "delete", "name": "fast.py"}})
        assert not (tmp_path / "indicators" / "fast.py").exists()
        again = await call(app, "write_indicator", {"operation": {"op": "create", "name": "fast.py", "content": USER_SMA}})
        assert again["check"]["error"] is None
        named = await call(app, "write_indicator", {"operation": {"op": "create", "name": "缠论 分型.py", "content": USER_SMA}})
        assert named["name"] == "缠论 分型.py"
        chart = await call(app, "write_chart", {"operation": {"op": "create", "symbol": "okx:BTC/USDT",
                                "timeframe": "1m", "indicators": [{"name": named["name"]}]}})
        assert chart["chartId"]
        await app.close()
    asyncio.run(main())


def test_yearly_chart_reads_prices_and_surviving_indicators_after_definition_deletion(tmp_path):
    async def main():
        market, app = opened(tmp_path)
        chart = await call(app, "write_chart", {"operation": {"op": "create", "symbol": "okx:BTC/USDT",
                           "timeframe": "1y", "indicators": [{"name": "my_sma.py"}, {"name": "volume"}]}})
        await call(app, "write_indicator", {"operation": {"op": "delete", "name": "my_sma.py"}})
        reading = asyncio.create_task(call(app, "read_data", {"chartId": chart["chartId"], "limit": 1}))
        session = await market.session(BTC["instrumentId"])
        feed = app.hub.feeds[session.params["series"]["seriesId"]]
        assert session.params["series"]["timeframe"] == "1y" and feed.syminfo["period"] == "12M"
        rows = bars(7, 5)
        session.replace(rows)
        result = await reading
        assert result["rows"][-1][4] == rows[-1]["close"]
        missing, volume = result["indicators"]
        assert missing["error"]["code"] == "not_found" and volume["outputs"]
        await call(app, "write_chart", {"operation": {"op": "remove", "chartId": chart["chartId"],
                         "baseRevision": chart["revision"], "indicators": [volume["id"]]}})
        reading = asyncio.create_task(call(app, "read_data", {"chartId": chart["chartId"], "limit": 1}))
        (await market.session(BTC["instrumentId"])).replace(rows, fill="filling")
        partial = await asyncio.wait_for(reading, timeout=3)
        assert partial["rows"][0][4] == rows[-1]["close"] and partial["indicators"] == [missing]
        await app.close()
    asyncio.run(main())
