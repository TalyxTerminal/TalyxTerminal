"""Done criterion 8 (redirects): after Market redirects a merged instrument, the old id in chart
documents, watchlists and alerts becomes the new one when the workspace opens; charts do not
show as unsaved."""
import asyncio

from modules.chart.backend.app import ChartApp
from modules.chart.backend.document import COMMANDS
from modules.chart.backend.wire import (
    AlertCreate,
    ChartCommit,
    ChartCreate,
    ChartSave,
    WatchlistCreate,
)
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket, series_of
from vocabulary.identity import instrument_id

MERGED = {**BTC, "symbol": "BTC/USDT", "displaySymbol": "BTC/USDT", "identityVersion": 1, "base": "XBT"}
MERGED["instrumentId"] = instrument_id({key: value for key, value in MERGED.items() if key != "instrumentId"})


def test_criterion_8_redirected_ids_are_replaced_in_documents_watchlists_and_alerts(tmp_path):
    async def main():
        market = FakeMarket(instruments=(BTC, ETH, MERGED))
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        app.store.put_object("seeded", {"id": "w1"}, "w1")
        chart = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m"))
        state = await app.commit("w1", ChartCommit(chartId=chart["chartId"], baseRevision=1, commands=COMMANDS.validate_python([
            {"type": "drawing.add", "value": {"id": "d1", "tool": "hline", "points": [{"time": 1, "price": 5}]}},
            {"type": "comparison.add", "id": "c1", "instrumentId": ETH["instrumentId"]}])))
        state = await app.commit("w1", ChartCommit(chartId=chart["chartId"], baseRevision=state["revision"],
            commands=COMMANDS.validate_python([
                {"type": "selection.set", "instrumentId": MERGED["instrumentId"]},
                {"type": "drawing.add", "value": {"id": "d2", "tool": "hline", "points": [{"time": 1, "price": 6}]}},
                {"type": "selection.set", "instrumentId": BTC["instrumentId"]}])))
        await app.save("w1", ChartSave.model_validate({"charts": [{"chartId": chart["chartId"], "revision": state["revision"]}]}))
        listed = await app.create_watchlist("w1", WatchlistCreate.model_validate({"name": "L", "sections": [
            {"id": "a", "name": "", "members": [{"instrument": BTC}, {"instrument": ETH}]}]}))
        alert = await app.create_alert("w1", AlertCreate.model_validate({"spec": {
            "target": {"kind": "series", "instrument": BTC, "series": series_of(BTC)},
            "conditions": [{"left": {"kind": "price"}, "op": "greater", "right": {"kind": "value", "value": 1}}]}}))
        app.workspace_closed("w1")
        await app.close()

        market = FakeMarket(instruments=(BTC, ETH, MERGED))
        market.redirects = {BTC["instrumentId"]: MERGED["instrumentId"]}
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        app.workspace_opened("w1")
        await app.opening["w1"]
        stored = app.store.chart(chart["chartId"])
        for document in (stored["document"], stored["savedDocument"]):
            assert document["instrument"]["instrumentId"] == MERGED["instrumentId"]
            assert document["series"]["instrumentId"] == MERGED["instrumentId"]
            assert document["series"]["seriesId"] == series_of(MERGED)["seriesId"]
            assert [key.split(":")[0] for key in document["drawings"]] == [MERGED["instrumentId"]]
            assert {drawing["id"] for drawing in next(iter(document["drawings"].values()))} == {"d1", "d2"}
            assert document["comparisons"][0]["instrument"]["instrumentId"] == ETH["instrumentId"]
        assert stored["revision"] == stored["savedRevision"] == state["revision"]  # not unsaved
        opened = app.charts.state(app.charts.entry("w1", chart["chartId"]))
        assert opened["document"]["instrument"]["instrumentId"] == MERGED["instrumentId"]
        members = [member["instrument"]["instrumentId"] for member in app.watchlists.get("w1", listed["id"])["sections"][0]["members"]]
        assert members == [MERGED["instrumentId"], ETH["instrumentId"]]
        moved = app.alerts.get("w1", alert["id"])
        assert moved["target"]["instrument"]["instrumentId"] == MERGED["instrumentId"]
        assert moved["target"]["series"]["seriesId"] == series_of(MERGED)["seriesId"]
        await app.close()
    asyncio.run(main())
