"""One chart's flow end to end over a stand-in Market: document, bars, forming bar, status,
indicators, commands, undo, switching symbol."""
import asyncio
import base64
import io
import json

import pyarrow.parquet as pq
import pytest

from modules.chart.backend.app import ChartApp
from modules.chart.backend.document import COMMANDS
from modules.chart.backend.wire import Backfill, ChartCommit, ChartCreate, ChartTarget, ExportChart
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket, series_of
from tests.unit.chart.test_pine_engine import bars


class Reader:
    def __init__(self, stream):
        self.stream = stream
        self.items = []

    async def until(self, predicate, timeout=60):
        async def wait():
            while True:
                item = await anext(self.stream)
                self.items.append(item)
                if predicate(item):
                    return item
        return await asyncio.wait_for(wait(), timeout)


def commit(chart_id, revision, *commands):
    return ChartCommit(chartId=chart_id, baseRevision=revision, commands=COMMANDS.validate_python(list(commands)))


def test_a_chart_flows_from_market_through_indicators(tmp_path):
    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        state = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m"))
        chart_id = state["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        first = await reader.until(lambda item: True)
        assert first["type"] == "document" and first["revision"] == 1
        rows = bars(1200, 3)
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:1000])
        replace = await reader.until(lambda item: item["type"] == "bars")
        assert replace["op"] == "replace" and len(replace["time"]) == 1000
        assert replace["priceFormat"] == {"precision": 1, "minMove": 0.1}
        assert replace["time"][0] == rows[0]["time"] and replace["close"][5] == rows[5]["close"]
        session.status("live")
        assert (await reader.until(lambda item: item["type"] == "status"))["state"] == "live"

        # An indicator: its replace follows, rows aligned with the bars.
        state = await app.commit("w1", commit(chart_id, 1, {"type": "indicator.add", "value": {
            "id": "rsi", "definition": {"source": "builtin", "id": "rsi"}, "paneId": "pane-rsi"}}))
        assert state["revision"] == 2 and [pane["id"] for pane in state["document"]["panes"]] == ["price", "pane-rsi"]
        indicator = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi")
        assert indicator["op"] == "replace" and indicator["time"] == replace["time"]
        assert len(indicator["values"]["rsi"]) == 1000
        assert any(meta["kind"] == "hline" for meta in indicator["meta"])
        assert indicator["behindChart"] is True  # Pine v6: behind the bars unless the script says otherwise

        # The forming bar, then its close.
        session.update({**rows[1000], "close": rows[1000]["open"]})
        update = await reader.until(lambda item: item["type"] == "update")
        assert update["bar"]["time"] == rows[1000]["time"]
        assert (await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi"))["op"] == "update"
        session.append([rows[1000]])
        append = await reader.until(lambda item: item["type"] == "bars")
        assert append["op"] == "append" and append["time"] == [rows[1000]["time"]]
        indicator = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi" and item["op"] == "append")
        assert indicator["time"] == [rows[1000]["time"]]

        # Export: the closed bars and every indicator output, in three formats (the chart's default Volume computed too).
        def volume(item):
            return item["type"] == "indicator" and item["instanceId"] == "volume"
        if not any(volume(item) for item in reader.items):
            await reader.until(volume)
        exported = {kind: await app.export_chart("w1", ExportChart(chartId=chart_id, format=kind))
                    for kind in ("csv", "json", "parquet")}
        assert {item["rows"] for item in exported.values()} == {1001}
        assert exported["csv"]["filename"] == "BTC_USDT_1m.csv" and exported["csv"]["mediaType"] == "text/csv"
        records = json.loads(base64.b64decode(exported["json"]["data"]))
        assert records[0] == {"time": rows[0]["time"], "closeTime": rows[0]["time"] + 60_000,
                              **{key: rows[0][key] for key in ("open", "high", "low", "close", "volume")},
                              "volume: volume": rows[0]["volume"], "rsi: rsi": None}
        assert records[-1]["rsi: rsi"] == indicator["values"]["rsi"][0]
        header = base64.b64decode(exported["csv"]["data"]).decode().splitlines()[0]
        assert header == "time,closeTime,open,high,low,close,volume,volume: volume,rsi: rsi"
        parquet = pq.read_table(io.BytesIO(base64.b64decode(exported["parquet"]["data"])))
        assert parquet.num_rows == 1001 and parquet.column("rsi: rsi").to_pylist()[-1] == records[-1]["rsi: rsi"]

        # A drawing, undone.
        state = await app.commit("w1", commit(chart_id, 2, {"type": "drawing.add", "value": {
            "id": "d1", "tool": "trendline", "points": [{"time": rows[10]["time"], "price": 100},
                                                        {"time": rows[20]["time"], "price": 101}]}}))
        assert state["document"]["drawings"][state["drawingGroup"]][0]["id"] == "d1"
        state = await app.undo("w1", ChartTarget(chartId=chart_id))
        assert state["revision"] == 4 and state["document"]["drawings"] == {} and state["canRedo"]
        assert (await reader.until(lambda item: item["type"] == "document" and item["revision"] == 4))

        # Another symbol: a new subscription, indicators recompute there, drawings are per symbol.
        state = await app.commit("w1", commit(chart_id, 4, {"type": "selection.set", "instrumentId": ETH["instrumentId"]}))
        assert state["document"]["series"]["instrumentId"] == ETH["instrumentId"]
        assert state["document"]["series"]["timeframe"] == "1m"
        eth = await market.session(ETH["instrumentId"])
        eth.replace(bars(500, 8))
        replace = await reader.until(lambda item: item["type"] == "bars")
        assert len(replace["time"]) == 500
        indicator = await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "rsi")
        assert indicator["op"] == "replace" and len(indicator["time"]) == 500
        assert market.sessions[0].closed  # BTC's subscription ended with its last reader
        await app.close()
    asyncio.run(main())


def test_bars_do_not_wait_for_the_price_increment(tmp_path):
    """Market's terms may need a source request: the bars go out with the data's format, and a
    different increment arriving later goes out as its own price_format item."""
    async def main():
        market = FakeMarket(increment="0.5")
        market.terms_gate = asyncio.Event()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        rows = [{**row, "close": round(row["close"], 2)} for row in bars(50, 4)]
        (await market.session(BTC["instrumentId"])).replace(rows)
        first = await reader.until(lambda item: item["type"] == "bars")
        assert first["priceFormat"]["minMove"] != 0.5 and len(first["time"]) == 50
        market.terms_gate.set()
        changed = await reader.until(lambda item: item["type"] in ("bars", "price_format"))
        assert changed == {"type": "price_format", "priceFormat": {"precision": 1, "minMove": 0.5}}

        # Market has nothing older: scrolling back changes nothing and resends nothing.
        result = await app.backfill("w1", Backfill(chartId=chart_id, bars=500))
        assert result == {"loaded": 0, "reachedStart": True, "limitReached": False}
        assert len(market.sessions) == 1
        await app.close()
    asyncio.run(main())


def test_a_cold_window_shows_markets_newest_page_then_its_whole_window(tmp_path):
    """Market's newest page (fill.state filling) goes out at once; the one replace with the whole
    window that follows goes out as a replace, with no reading of older bars by Chart."""
    async def main():
        market = FakeMarket()
        rows = bars(12_000, 5)
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[-300:], has_more_before=True, fill="filling")
        first = await reader.until(lambda item: item["type"] == "bars")
        assert len(first["time"]) == 300
        session.replace(rows[-10_000:], has_more_before=True)
        filled = await reader.until(lambda item: item["type"] == "bars")
        assert filled["op"] == "replace" and len(filled["time"]) == 10_000
        assert filled["time"] == [row["time"] for row in rows[-10_000:]] and filled["hasMoreBefore"]
        await app.close()
    asyncio.run(main())


def test_every_indicator_has_a_row_for_every_bar_after_a_fill_and_appends(tmp_path):
    """Market fills the window while bars close: every indicator restarts on the whole window and
    then follows the appends; its closed rows match the bars one for one, and a reader that joins
    later (a reload) gets the same."""
    async def main():
        market = FakeMarket()
        rows = bars(10_300, 14)
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        for revision, name in enumerate(("rsi", "boll"), start=1):
            await app.commit("w1", commit(chart_id, revision, {"type": "indicator.add", "value": {
                "id": name, "definition": {"source": "builtin", "id": name}, "paneId": "price"}}))
        reader = Reader(await app.chart_stream("w1", chart_id))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[7:10_000], has_more_before=True, fill="filling")
        await reader.until(lambda item: item["type"] == "indicator" and item["instanceId"] == "boll")
        session.append(rows[10_000:10_003])  # while Market fills the window
        session.replace(rows[:10_003])
        await reader.until(lambda item: item["type"] == "bars" and item["op"] == "replace" and len(item["time"]) > 10_000)
        for row in rows[10_003:10_010]:
            session.append([row])
            await session.drained()
        expected = [row["time"] for row in rows[:10_010]]

        def closed_rows(items, name):
            times = []
            for item in items:
                if item["type"] == "indicator" and item["instanceId"] == name:
                    if item["op"] == "replace":
                        times = list(item["time"])
                    elif item["op"] == "append":
                        times.extend(item["time"])
            return times

        def complete(items):
            return all(closed_rows(items, name)[-len(expected):] == expected for name in ("rsi", "boll"))

        await reader.until(lambda item: complete(reader.items))
        bars_times = []
        for item in reader.items:
            if item["type"] == "bars":
                bars_times = list(item["time"]) if item["op"] == "replace" else bars_times + item["time"]
        assert bars_times[-len(expected):] == expected
        for name in ("rsi", "boll"):
            assert closed_rows(reader.items, name) == bars_times, name
        rows_follow_bars(reader.items)

        later = Reader(await app.chart_stream("w1", chart_id))  # a reload, while bars keep closing
        session.append([rows[10_010]])
        expected.append(rows[10_010]["time"])
        await later.until(lambda item: complete(later.items))
        for name in ("rsi", "boll"):
            assert closed_rows(later.items, name) == expected, name
        await app.close()
    asyncio.run(main())


def rows_follow_bars(items) -> None:
    """No indicator or comparison row for a time goes out before a bar for that time."""
    known: set[int] = set()
    for item in items:
        if item["type"] == "bars":
            known = set(item["time"]) if item["op"] == "replace" else known | set(item["time"])
        elif item["type"] == "update":
            known.add(item["bar"]["time"])
        elif item["type"] in ("indicator", "comparison") and item["op"] != "replace":
            assert set(item["time"]) <= known, f"{item['type']} {item['op']} at {item['time']} before its bar"


def test_the_forming_bar_of_a_replace_goes_out_before_rows_computed_from_it(tmp_path):
    """Market's replace carries the forming bar: it goes out as an update right after the
    bars, before any indicator row for its time (the frontend has no bar for it otherwise)."""
    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        await app.commit("w1", commit(chart_id, 1, {"type": "indicator.add", "value": {
            "id": "rsi", "definition": {"source": "builtin", "id": "rsi"}, "paneId": "pane-rsi"}}))
        reader = Reader(await app.chart_stream("w1", chart_id))
        rows = bars(400, 7)
        (await market.session(BTC["instrumentId"])).replace(rows[:300], forming=rows[300])
        await reader.until(lambda item: item["type"] == "indicator" and item["op"] == "update")
        rows_follow_bars(reader.items)
        update = next(item for item in reader.items if item["type"] == "update")
        assert update["bar"]["time"] == rows[300]["time"]
        await app.close()
    asyncio.run(main())


def test_a_market_replace_that_only_adds_bars_goes_out_as_append(tmp_path):
    """Market merges a catch-up of more than 500 rows into a replace: when it repeats our last
    bars unchanged, the chart gets the new bars as an append, not the whole window again."""
    async def main():
        market = FakeMarket()
        rows = bars(1300, 6)
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[:1000])
        assert len((await reader.until(lambda item: item["type"] == "bars"))["time"]) == 1000
        session.replace(rows[300:1200])
        caught_up = await reader.until(lambda item: item["type"] == "bars")
        assert caught_up["op"] == "append" and caught_up["time"] == [row["time"] for row in rows[1000:1200]]
        changed = [{**row, "close": row["close"] + 1} if index == 50 else row for index, row in enumerate(rows[400:1300])]
        session.replace(changed)  # a revised bar: the whole window again
        revised = await reader.until(lambda item: item["type"] == "bars")
        assert revised["op"] == "replace" and len(revised["time"]) == 900
        await app.close()
    asyncio.run(main())


def test_a_stale_revision_conflicts_and_a_workspace_close_reverts(tmp_path):
    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"])))["chartId"]
        add = {"type": "display.update", "patch": {"theme": "dark"}}
        await app.commit("w1", commit(chart_id, 1, add))
        try:
            await app.commit("w1", commit(chart_id, 1, add))
            raise AssertionError("expected a conflict")
        except Exception as error:  # noqa: BLE001 -- the ContractError carries the current revision
            assert error.code == "conflict" and error.details == {"revision": 2}
        await app.save("w1", __import__("modules.chart.backend.wire", fromlist=["ChartSave"]).ChartSave(
            charts=[{"chartId": chart_id, "revision": 2}]))
        await app.commit("w1", commit(chart_id, 2, {"type": "display.update", "patch": {"theme": "light"}}))
        unsaved = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"])))["chartId"]
        app.workspace_closed("w1")
        reopened = app.charts.entry("w1", chart_id)
        assert reopened.revision == 2 and reopened.document.display == {"theme": "dark"}
        assert app.store.chart(unsaved) is None
        await app.close()
    asyncio.run(main())


def test_a_commit_whose_write_fails_leaves_the_chart_and_its_undo_as_they_were(tmp_path):
    """The document, revision and undo move only once the revision is on disk: the caller retries at the same base
    revision, and a restart reads what the chart showed."""
    import sqlite3

    async def main():
        app = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"])))["chartId"]
        await app.commit("w1", commit(chart_id, 1, {"type": "display.update", "patch": {"theme": "dark"}}))
        app.store.db.execute("CREATE TRIGGER full BEFORE UPDATE ON charts BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        light = {"type": "display.update", "patch": {"theme": "light"}}
        for attempt in (lambda: app.commit("w1", commit(chart_id, 2, light)),
                        lambda: app.undo("w1", ChartTarget(chartId=chart_id))):
            with pytest.raises(sqlite3.Error):
                await attempt()
            entry = app.charts.entry("w1", chart_id)
            assert (entry.revision, entry.document.display, len(entry.undo), entry.redo) == (2, {"theme": "dark"}, 1, [])
        app.store.db.execute("DROP TRIGGER full")
        state = await app.commit("w1", commit(chart_id, 2, light))  # the same base revision, no conflict
        assert state["revision"] == 3 and state["canUndo"]
        await app.close()
        again = ChartApp(FakeMarket(), tmp_path / "data", tmp_path / "indicators")
        stored = again.charts.entry("w1", chart_id)
        assert (stored.revision, stored.document.display) == (3, {"theme": "light"})
        await again.close()
    asyncio.run(main())


def test_routes_validate_bodies_and_responses(tmp_path):
    """The private routes as the frontend calls them: bodies and responses go through ``wire.ROUTES``."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.chart.backend.routes import router

    composed, market = {}, FakeMarket()
    (tmp_path / "indicators").mkdir(exist_ok=True)
    (tmp_path / "indicators" / "broken.py").write_text('"""@pyne"""\nfrom pynecore.lib import script\n\nundefined_name\n',
                                                       encoding="utf-8")

    @asynccontextmanager
    async def lifespan(api):
        app = composed["app"] = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        api.mount("/", router(app))
        yield
        await app.close()

    api = FastAPI(lifespan=lifespan)

    with TestClient(api, headers={"x-talyx-workspace": "w1"}) as client:
        created = client.post("/charts/create", json={}).json()
        assert created["revision"] == 1 and created["document"]["instrument"]["instrumentId"] == BTC["instrumentId"]
        assert created["document"]["series"]["timeframe"] == "1d"
        chart_id = created["chartId"]
        state = client.post("/charts/commit", json={"chartId": chart_id, "baseRevision": 1, "commands": [
            {"type": "display.update", "patch": {"theme": "dark"}}]}).json()
        assert state["revision"] == 2 and state["canUndo"]
        assert client.post("/charts/undo", json={"chartId": chart_id}).json()["revision"] == 3
        assert client.post("/charts/open", json={"chartId": chart_id}).json()["canRedo"]
        assert client.post("/charts/save", json={"charts": [{"chartId": chart_id, "revision": 3}]}).json() == {}
        stale = client.post("/charts/commit", json={"chartId": chart_id, "baseRevision": 1, "commands": []})
        assert stale.status_code >= 400
        assert client.post("/charts/commit", json={"chartId": chart_id, "baseRevision": 3,
                                                   "commands": [{"type": "nope"}]}).status_code == 422
        catalog = client.post("/indicators/catalog", json={}).json()
        assert catalog["folder"] == str((tmp_path / "indicators").resolve())
        entries = catalog["entries"]
        broken = next(entry for entry in entries if entry["id"] == "broken.py")
        assert broken["line"] == 4 and "undefined_name" in broken["error"] and "line" not in broken["error"]
        assert {"rsi", "macd", "volume"} <= {entry["id"] for entry in entries if entry["source"] == "builtin"}
        rsi = next(entry for entry in entries if entry["id"] == "rsi")
        assert (rsi["title"], rsi["shortTitle"], rsi["category"]) == ("Relative Strength Index", "RSI", "Oscillators")
        assert {entry["category"] for entry in entries if entry["source"] == "builtin"} == {"Trend", "Oscillators", "Volume", "Volatility",
                                                            "Moving Averages"}

        # Alert defaults: Chart's settings as they apply in each open workspace, in its snapshot; a change is sent.
        client.portal.call(composed["app"].read_alert_defaults, "w2")
        other = composed["app"].workspaces.subscribe("w2")
        snapshot = client.portal.call(anext, other)
        assert snapshot["alertDefaults"] == {"toast": True, "sound": True}
        market.settings["alertPopup"] = False
        client.portal.call(composed["app"].alert_defaults_changed, "global")
        assert client.portal.call(anext, other) == {"type": "alert_defaults", "value": {"toast": False, "sound": True}}
        assert composed["app"].snapshot("w2")["alertDefaults"] == {"toast": False, "sound": True}

        # Templates: global, sent to every workspace, in every snapshot.
        content = {"indicators": [{"definition": {"source": "builtin", "id": "rsi"}, "params": {"period": 21}}],
                   "panes": [{"height": 1}]}
        template = client.post("/templates/save", json={"kind": "indicators", "name": "My RSI",
                                                         "content": content}).json()
        assert template == {"id": template["id"], "kind": "indicators", "name": "My RSI", "content": content}
        assert client.portal.call(anext, other) == {"type": "template", "value": template}
        renamed = client.post("/templates/save", json={"templateId": template["id"], "kind": "indicators",
                                                        "name": "RSI 21", "content": content}).json()
        assert renamed["id"] == template["id"]
        assert client.portal.call(anext, other)["value"]["name"] == "RSI 21"
        assert composed["app"].snapshot("w1")["templates"] == [renamed]
        assert client.post("/templates/delete", json={"templateId": renamed["id"]}).json() == {}
        assert client.portal.call(anext, other) == {"type": "template_removed", "id": renamed["id"]}

        # Alerts through the routes.
        made = client.post("/alerts/create", json={"spec": {
            "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
            "conditions": [{"left": {"kind": "price"}, "op": "greater", "right": {"kind": "value", "value": 1}}]}}).json()
        assert made["state"] == "active" and made["revision"] == 1 and made["channels"]["toast"]
        assert client.post("/alerts/state", json={"alertId": made["id"], "revision": 1, "state": "paused"}).json()["state"] == "paused"
        assert client.post("/alerts/log", json={"alertId": made["id"]}).json() == {"events": [], "next": None}
        assert client.post("/alerts/delete", json={"alertId": made["id"]}).json() == {}

        # Screens through the routes.
        screen = client.post("/screens/save", json={"spec": {
            "name": "S", "scope": {"kind": "instruments", "instruments": [BTC]}, "timeframe": "1m",
            "conditions": [{"left": {"kind": "price"}, "op": "greater", "right": {"kind": "value", "value": 1}}]}}).json()
        assert screen["revision"] == 1 and screen["lastRun"] is None
        assert client.post("/screens/delete", json={"screenId": screen["id"]}).json() == {}

        # Watchlists through the routes.
        listed = client.post("/watchlists/create", json={"name": "Mine", "sections": [
            {"id": "a", "name": "", "members": [{"instrument": BTC}]}]}).json()
        assert listed["revision"] == 1 and listed["sections"][0]["members"][0]["instrument"] == BTC
        renamed = client.post("/watchlists/update", json={"watchlistId": listed["id"], "revision": 1, "name": "Ours"})
        assert renamed.json()["name"] == "Ours"
        assert client.post("/watchlists/delete", json={"watchlistId": listed["id"]}).json() == {}

        # An imported series: the import set is part of the series and stays when the timeframe changes.
        imported = client.post("/charts/create", json={"instrumentId": ETH["instrumentId"], "timeframe": "1h",
                                                       "importId": "imp-1"}).json()
        assert imported["document"]["series"]["importId"] == "imp-1"
        switched = client.post("/charts/commit", json={"chartId": imported["chartId"], "baseRevision": 1, "commands": [
            {"type": "selection.set", "timeframe": "4h"}]}).json()
        assert switched["document"]["series"]["importId"] == "imp-1"
        assert switched["document"]["series"]["timeframe"] == "4h"


def test_after_a_document_only_its_series_items_follow_through_fast_switches(tmp_path, monkeypatch):
    """15m → 30m → 15m faster than the 30m feed opens: the switch to 30m finishes after the second 15m document is out,
    and its bars, forming bar, rows and a result its unbound indicator sends late must not follow that document."""
    from modules.chart.backend import flows

    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="15m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))

        async def session_of(timeframe, count=1):
            for _ in range(500):
                found = [item for item in market.sessions if item.params["series"]["timeframe"] == timeframe]
                if len(found) >= count:
                    return found[count - 1]
                await asyncio.sleep(0.01)
            raise AssertionError(f"no {timeframe} session")

        quarter = bars(300, 5)
        half = [{**row, "time": row["time"] + 10**9, "closeTime": row["closeTime"] + 10**9} for row in bars(300, 6)]
        times = {"15m": {row["time"] for row in quarter}, "30m": {row["time"] for row in half}}
        (await session_of("15m")).replace(quarter[:290])
        await reader.until(lambda item: item["type"] == "bars")
        await app.commit("w1", commit(chart_id, 1, {"type": "indicator.add", "value": {
            "id": "rsi", "definition": {"source": "builtin", "id": "rsi"}, "paneId": "pane-rsi"}}))
        await reader.until(lambda item: item["type"] == "indicator" and item["op"] == "replace")

        # Both feeds open only when the test lets them; every binding made is kept.
        gates = {"30m": asyncio.Event(), "15m": asyncio.Event()}
        acquire = app.hub.acquire

        async def slow_acquire(instrument, series, **options):
            await gates[series["timeframe"]].wait()
            return await acquire(instrument, series, **options)
        monkeypatch.setattr(app.hub, "acquire", slow_acquire)
        made = []
        bind = flows.IndicatorBinding.bind

        async def kept(self, feed):
            made.append(self)
            await bind(self, feed)
        monkeypatch.setattr(flows.IndicatorBinding, "bind", kept)

        await app.commit("w1", commit(chart_id, 2, {"type": "selection.set", "timeframe": "30m"}))
        await app.commit("w1", commit(chart_id, 3, {"type": "selection.set", "timeframe": "15m"}))
        await reader.until(lambda item: item["type"] == "document" and item["revision"] == 4)

        # The 30m switch finishes now, after the 15m document: its feed fills and moves on.
        gates["30m"].set()
        thirty = await session_of("30m")
        thirty.replace(half[:290], forming=half[290])
        await asyncio.sleep(1)
        thirty.append(half[290:292])
        thirty.update(half[292])
        await asyncio.sleep(1)
        late = next(binding for binding in made if binding.feed is not None and binding.feed.series["timeframe"] == "30m")

        # The 15m switch: a new subscription, its replace and its rows.
        gates["15m"].set()
        (await session_of("15m", 2)).replace(quarter[:295])
        await reader.until(lambda item: item["type"] == "indicator" and item["op"] == "replace"
                           and set(item["time"]) <= times["15m"] and len(item["time"]) == 295)
        late({"op": "append", "time": [half[292]["time"]], "values": {"rsi": [50.0]}, "colors": {}})
        (await session_of("15m", 2)).update(quarter[295])
        await reader.until(lambda item: item["type"] == "update" and item["bar"]["time"] == quarter[295]["time"])

        shown = None
        for item in reader.items:
            if item["type"] == "document":
                shown = item["document"]["series"]["timeframe"]
            elif item["type"] in ("bars", "indicator"):
                assert set(item["time"]) <= times[shown], (shown, item["type"], item.get("op"))
            elif item["type"] == "update":
                assert item["bar"]["time"] in times[shown], (shown, item)
    asyncio.run(main())


def test_a_request_security_feed_reaches_back_to_its_start_within_its_cap(tmp_path):
    """Intrabars for a long chart window: whole (supplied) with Market's window, then older bars from
    Market until the feed starts at the chart's first bar, never more than its cap."""
    async def main():
        from modules.chart.backend.feeds import Hub
        from modules.chart.backend.marketdata import Market
        market = FakeMarket()
        rows = bars(30_000, 6)
        market.older = rows[:-1_000]
        hub = Hub(Market(market))
        series = series_of(BTC, "1m")

        async def window(start, cap):
            acquiring = asyncio.ensure_future(hub.acquire(BTC, series, start=start, cap=cap, whole=True))
            session = await market.session(BTC["instrumentId"])
            session.replace(rows[-1_000:], has_more_before=True)
            feed = await acquiring
            for _ in range(200):  # the reach back follows the whole window
                if feed.columns["time"][0] <= start or len(feed.columns["time"]) >= cap:
                    break
                await asyncio.sleep(0.01)
            times = list(feed.columns["time"])
            await hub.release(feed)
            return times

        reached = await window(rows[-15_000]["time"], 200_000)
        assert reached[0] <= rows[-15_000]["time"] and reached == [row["time"] for row in rows[-len(reached):]]
        capped = await window(rows[0]["time"], 12_000)
        assert len(capped) == 12_000 and capped == [row["time"] for row in rows[-12_000:]]
        await hub.close()
    asyncio.run(main())


async def drawn(collector) -> None:
    """Until a replace carries the volume profile's boxes (one on Market's newest page may come first)."""
    for _ in range(600):
        if any(item["op"] == "replace" and (item.get("drawings") or {}).get("boxes") for item in collector.items):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(collector.items)


def test_a_whole_window_that_only_adds_to_the_newest_page_is_whole(tmp_path, monkeypatch):
    """Market's fill found nothing older (or failed): its whole window is the newest page and the bar
    closed since. The intrabar feed is whole then, and the volume profile reading it draws."""
    monkeypatch.setenv("TALYX_PINE_NEED_SECONDS", "8")  # a feed never supplied fails the script by then

    async def main():
        from modules.chart.backend.feeds import Hub
        from modules.chart.backend.marketdata import Market
        from modules.chart.backend.pine.engine import PineEngine
        from modules.chart.backend.pine.indicators import Catalog, Indicators
        from tests.unit.chart.test_pine_engine import Collector
        market = FakeMarket()
        hub = Hub(Market(market))
        engine = PineEngine(tmp_path / "logs")
        indicators = Indicators(engine, Catalog(engine, tmp_path / "none"), hub)
        minute = bars(1_500, 8)
        five = [{**row, "time": minute[index * 5]["time"]} for index, row in enumerate(bars(300, 9))]
        acquiring = asyncio.ensure_future(hub.acquire(BTC, series_of(BTC, "5m")))
        (await market.session(BTC["instrumentId"])).replace(five)
        feed = await acquiring
        collector = Collector()
        await indicators.attach(feed, {"source": "builtin", "id": "session_volume_profile"}, {}, collector)
        for _ in range(500):
            if len(market.sessions) > 1:
                break
            await asyncio.sleep(0.02)
        intrabars = market.sessions[-1]
        intrabars.replace(minute[:-2], fill="filling")
        intrabars.replace(minute[:-1], forming=minute[-1], fill="failed")
        await drawn(collector)
        await engine.close()
        await hub.close()
    asyncio.run(main())


def test_a_feed_whose_opening_is_cancelled_is_not_kept(tmp_path):
    """request.security lets go of a feed while the hub still opens it: the next one asked for with
    that key subscribes afresh and gets its bars."""
    async def main():
        from contextlib import suppress

        from modules.chart.backend.feeds import Hub
        from modules.chart.backend.marketdata import Market
        market = FakeMarket()
        hub = Hub(Market(market))
        series = series_of(BTC, "1m")
        gate, subscribe = asyncio.Event(), market.subscribe

        async def held(name, params):
            await gate.wait()
            return await subscribe(name, params)
        market.subscribe = held
        opening = asyncio.ensure_future(hub.acquire(BTC, series, start=0))
        await asyncio.sleep(0.1)
        opening.cancel()
        with suppress(asyncio.CancelledError):
            await opening
        gate.set()
        acquiring = asyncio.ensure_future(hub.acquire(BTC, series, start=0))
        (await market.session(BTC["instrumentId"])).replace(bars(10, 3))
        feed = await acquiring
        await asyncio.wait_for(feed.ready.wait(), 5)
        await hub.close()
    asyncio.run(main())


def test_live_intrabars_while_market_fills_their_window_wait_for_it(tmp_path):
    """A tick on the intrabar feed before Market's whole window (its first page says filling) is
    in that window when it comes; the volume profile then draws."""
    async def main():
        from modules.chart.backend.feeds import Hub
        from modules.chart.backend.marketdata import Market
        from modules.chart.backend.pine.engine import PineEngine
        from modules.chart.backend.pine.indicators import Catalog, Indicators
        from tests.unit.chart.test_pine_engine import Collector
        market = FakeMarket()
        hub = Hub(Market(market))
        engine = PineEngine(tmp_path / "logs")
        indicators = Indicators(engine, Catalog(engine, tmp_path / "none"), hub)
        minute = bars(1_500, 8)
        five = [{**row, "time": minute[index * 5]["time"]} for index, row in enumerate(bars(300, 9))]
        acquiring = asyncio.ensure_future(hub.acquire(BTC, series_of(BTC, "5m")))
        (await market.session(BTC["instrumentId"])).replace(five)
        feed = await acquiring
        collector = Collector()
        await indicators.attach(feed, {"source": "builtin", "id": "session_volume_profile"}, {}, collector)
        for _ in range(500):
            if len(market.sessions) > 1:
                break
            await asyncio.sleep(0.02)
        intrabars = market.sessions[-1]
        intrabars.replace(minute[-100:-1], has_more_before=True, fill="filling")
        intrabars.update(minute[-1])
        intrabars.replace(minute[:-1], forming=minute[-1])
        await drawn(collector)
        await engine.close()
        await hub.close()
    asyncio.run(main())


def test_a_reach_back_while_market_replaces_the_window_keeps_bars_in_order(tmp_path):
    """A chart scrolled back (or a 5D view) reaches back from Market's newest page; Market's whole window
    replaces it while an older page is on its way. The older page is not put before the new window."""
    async def main():
        from modules.chart.backend.feeds import Hub
        from modules.chart.backend.marketdata import Market
        market = FakeMarket()
        rows = bars(6_000, 7)
        market.older = rows
        market.read_gate = asyncio.Event()
        hub = Hub(Market(market))
        acquiring = asyncio.ensure_future(hub.acquire(BTC, series_of(BTC, "1m")))
        session = await market.session(BTC["instrumentId"])
        session.replace(rows[-300:], has_more_before=True, fill="filling")
        feed = await acquiring
        reaching = asyncio.ensure_future(feed.extend(bars=1_000))
        await asyncio.sleep(0.05)  # the older page is asked for, before the newest page's first bar
        session.replace(rows[-3_000:], has_more_before=True)
        for _ in range(200):
            if len(feed.columns["time"]) >= 3_000:
                break
            await asyncio.sleep(0.01)
        market.read_gate.set()
        await reaching
        times = feed.columns["time"]
        assert times == sorted(set(times)) and times[-1] == rows[-1]["time"], (len(times), times[:3])
        await hub.release(feed)
        await hub.close()
    asyncio.run(main())


def test_box_charts_without_a_box_size_open_on_atr_14_boxes(tmp_path):
    """Renko, Kagi, P&F and Range open with TradingView's default box: ATR(14) at the window's last
    bar (Pine's ta.atr) in whole ticks; Kagi reverses by that much. Range ticks, when set, are used."""
    from modules.chart.backend.feeds import COLUMNS
    from modules.chart.backend.projection import default_box_size

    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        rows = bars(300, 3)
        (await market.session(BTC["instrumentId"])).replace(rows)
        await reader.until(lambda item: item["type"] == "bars")
        atr, previous = None, rows[0]["close"]
        for index, row in enumerate(rows):
            true_range = max(row["high"] - row["low"], abs(row["high"] - previous), abs(row["low"] - previous))
            previous = row["close"]
            atr = true_range if index == 0 else (atr * index + true_range) / (index + 1) if index < 14 else (atr * 13 + true_range) / 14
        columns = [[row[name] for row in rows] for name in COLUMNS]
        assert default_box_size("renko", columns, 0.1, 3) == round(atr / 0.1) * 0.1
        assert default_box_size("kagi", columns, 0.1, 3) == round(atr / 3 / 0.1) * 0.1
        for revision, kind in enumerate(("renko", "kagi", "point_figure", "range"), start=1):
            await app.commit("w1", commit(chart_id, revision, {"type": "chart_type.set", "chartType": kind}))
            item = await reader.until(lambda item: item["type"] in ("bars", "status"))
            assert item["type"] == "bars" and item["op"] == "replace" and item["time"], (kind, item)
        box = round(atr / 0.1) * 0.1
        assert app.hub.feeds[next(key for key in app.hub.feeds if "|range|" in key)].spec.range_size == box
        projection = {"source": "close", "boxSize": None, "reversalBoxes": 3, "lineCount": 3, "rangeTicks": 7}
        await app.commit("w1", commit(chart_id, 5, {"type": "chart_type.set", "chartType": "range", "projection": projection}))
        await reader.until(lambda item: item["type"] == "bars")
        assert app.hub.feeds[next(key for key in app.hub.feeds if "rangeTicks=7" in key)].spec.range_size == 7 * 0.1
        await app.close()
    asyncio.run(main())


def test_a_chart_type_that_fails_to_open_lets_the_next_switch_and_the_close_through(tmp_path, monkeypatch):
    """A failed switch lets go of the old feed: switching on and closing the chart do not let go of it again."""
    from talyx_sdk import ContractError

    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart_id = (await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m")))["chartId"]
        reader = Reader(await app.chart_stream("w1", chart_id))
        (await market.session(BTC["instrumentId"])).replace(bars(300, 3))
        await reader.until(lambda item: item["type"] == "bars")
        acquire = app.hub.acquire

        async def failing(*args, **kwargs):
            if kwargs["chart_type"] == "kagi":
                raise ContractError("unavailable", "kagi does not open")
            return await acquire(*args, **kwargs)
        monkeypatch.setattr(app.hub, "acquire", failing)
        await app.commit("w1", commit(chart_id, 1, {"type": "chart_type.set", "chartType": "kagi"}))
        failed = await reader.until(lambda item: item["type"] == "status")
        assert failed["state"] == "failed" and failed["problem"]["message"] == "kagi does not open"
        await app.commit("w1", commit(chart_id, 2, {"type": "chart_type.set", "chartType": "candles"}))
        (await market.session(BTC["instrumentId"])).replace(bars(300, 3))  # the series opens again
        item = await reader.until(lambda item: item["type"] in ("bars", "status") and item.get("state") != "live")
        assert item["type"] == "bars" and item["op"] == "replace", item
        await app.close()
        assert app.hub.feeds == {}
    asyncio.run(main())


def test_primary_ticks_keep_flowing_while_a_comparison_subscribes(tmp_path):
    async def main():
        market = FakeMarket()
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        chart = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1h"))
        reader = Reader(await app.chart_stream("w1", chart["chartId"]))
        session = await market.session(BTC["instrumentId"])
        rows = bars(5, 3)
        session.replace(rows[:3])
        await reader.until(lambda item: item["type"] == "bars")
        entered, release = asyncio.Event(), asyncio.Event()
        subscribe = market.subscribe

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await subscribe(*args, **kwargs)

        market.subscribe = delayed
        await app.commit("w1", commit(chart["chartId"], 1, {"type": "comparison.add", "id": "eth",
                                                             "instrumentId": ETH["instrumentId"]}))
        await entered.wait()
        session.update(rows[3])
        session.update(rows[4])
        received = await reader.until(lambda item: item["type"] == "status" or
                                      item["type"] == "update" and item["bar"]["time"] == rows[4]["time"])
        assert received["type"] == "update"
        release.set()
        (await market.session(ETH["instrumentId"])).replace(rows)
        assert (await reader.until(lambda item: item["type"] == "comparison"))["value"] == [
            row["close"] for row in rows[:3]]
        await app.close()
    asyncio.run(main())
