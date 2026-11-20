"""Done criterion 8 (instrument identity): drawings stay with their symbol and price basis across
switches; a chart, a watchlist alert and a screen read a symbol on the same series (Market's
default for it); an alert made from a chart follows that chart's series. Redirects:
test_redirects.py."""
import asyncio
from types import SimpleNamespace

from talyx_sdk.process import Caller

from modules.chart.backend import actions
from modules.chart.backend.app import ChartApp
from modules.chart.backend.wire import ChartCreate, ScreenSave, WatchlistCreate
from tests.unit.chart.fake_market import BTC, ETH, FakeMarket, series_of
from tests.unit.chart.test_chart_flow import commit
from vocabulary.instruments import MarketInstrument

AAPL = MarketInstrument(identityVersion=2, instrumentId="vi2_" + "a" * 32, venueId="US", symbol="AAPL",
                        displaySymbol="AAPL", marketType="spot", assetClass="equity", securityType="common_stock",
                        calendarId="XNYS", currency="USD", adjustmentEligible=True).to_dict()
USER = SimpleNamespace(caller=Caller("quant", "w1", "module"), workspace_id="w1")
ABOVE_ONE = [{"left": {"kind": "price"}, "op": "greater", "right": {"kind": "value", "value": 1}}]


class RecordingMarket(FakeMarket):
    """Remembers the series of every history read (screens read through ``data.read``)."""

    def __init__(self):
        super().__init__(instruments=(BTC, ETH, AAPL))
        self.read_series: list[dict] = []

    async def read(self, value):
        self.read_series.append(value["series"])
        return await super().read(value)


def drawing(drawing_id: str, time: int, price: float) -> dict:
    return {"type": "drawing.add", "value": {"id": drawing_id, "tool": "hline", "points": [{"time": time, "price": price}]}}


def shown(state: dict) -> list[str]:
    return [item["id"] for item in state["document"]["drawings"].get(state["drawingGroup"], [])]


def test_criterion_8_a_drawing_stays_with_its_symbol_and_price_basis(tmp_path):
    async def main():
        app = ChartApp(RecordingMarket(), tmp_path / "data", tmp_path / "indicators")
        state = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m"))
        chart = state["chartId"]
        state = await app.commit("w1", commit(chart, 1, drawing("d1", 1_000, 100)))
        assert shown(state) == ["d1"]

        state = await app.commit("w1", commit(chart, 2, {"type": "selection.set", "instrumentId": ETH["instrumentId"]}))
        assert shown(state) == []  # B shows none of A's drawings
        state = await app.commit("w1", commit(chart, 3, drawing("d2", 2_000, 50)))
        assert shown(state) == ["d2"]

        state = await app.commit("w1", commit(chart, 4, {"type": "selection.set", "instrumentId": BTC["instrumentId"]}))
        assert shown(state) == ["d1"]  # A's comes back, untouched by what was drawn on B
        assert state["document"]["drawings"][state["drawingGroup"]][0]["points"] == [{"time": 1_000, "price": 100}]
        state = await app.commit("w1", commit(chart, 5, {"type": "selection.set", "timeframe": "1h"}))
        assert shown(state) == ["d1"]  # another timeframe of the same symbol: the same drawings
        state = await app.commit("w1", commit(chart, 6, {"type": "selection.set", "priceBasis": "mark"}))
        assert shown(state) == []  # the mark price is another series of drawings (TradingView)

        stock = await app.create_chart("w1", ChartCreate(instrumentId=AAPL["instrumentId"], timeframe="1d"))
        state = await app.commit("w1", commit(stock["chartId"], 1, drawing("d3", 3_000, 180)))
        state = await app.commit("w1", commit(stock["chartId"], 2, {"type": "selection.set", "adjustment": "raw"}))
        assert state["document"]["series"]["adjustment"] == "raw"
        assert shown(state) == ["d3"]  # switching the adjustment keeps the drawings
        await app.close()
    asyncio.run(main())


def test_criterion_8_chart_watchlist_alert_and_screen_read_a_symbol_on_the_same_series(tmp_path):
    async def main():
        market = RecordingMarket()
        market.histories[AAPL["instrumentId"]] = [
            {"time": 1_700_000_000_000 + index * 60_000, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
             "volume": 5.0} for index in range(5)]
        app = ChartApp(market, tmp_path / "data", tmp_path / "indicators")
        app.store.put_object("seeded", {"id": "w1"}, "w1")
        app.workspace_opened("w1")
        await app.opening["w1"]

        chart = await app.create_chart("w1", ChartCreate(instrumentId=AAPL["instrumentId"], timeframe="1d"))
        listed = await app.create_watchlist("w1", WatchlistCreate.model_validate({"name": "L", "sections": [
            {"id": "s1", "name": "", "members": [{"instrument": AAPL}]}]}))
        await call(app, "write_alert", {"operation": {"op": "create", "target": {
            "watchlistId": listed["id"], "timeframe": "1d"}, "conditions": ABOVE_ONE}})
        watched = await market.session(AAPL["instrumentId"])
        screen = await app.save_screen("w1", ScreenSave.model_validate({"spec": {
            "name": "S", "scope": {"kind": "instruments", "instruments": [AAPL]}, "timeframe": "1d",
            "conditions": ABOVE_ONE}}))
        app.screens.run("w1", screen["id"])
        await app.screens.runs[screen["id"]]

        default = series_of(AAPL, "1d")
        assert default["adjustment"] == "split"  # Market's default for US stocks
        assert chart["document"]["series"] == watched.params["series"] == market.read_series[-1] == default
        await app.close()
    asyncio.run(main())


def test_criterion_8_an_alert_made_from_a_chart_follows_the_charts_series(tmp_path):
    async def main():
        app = ChartApp(RecordingMarket(), tmp_path / "data", tmp_path / "indicators")
        chart = await app.create_chart("w1", ChartCreate(instrumentId=AAPL["instrumentId"], timeframe="1d"))
        state = await app.commit("w1", commit(chart["chartId"], 1, {"type": "selection.set", "adjustment": "raw"}))
        made = await call(app, "write_alert", {"operation": {"op": "create", "target": {"chartId": chart["chartId"]},
                                                             "conditions": ABOVE_ONE}})
        target = app.alerts.get("w1", made["alert"]["id"])["target"]
        assert target["series"] == state["document"]["series"]
        assert target["series"]["adjustment"] == "raw"  # what the user sees on the chart, not the default
        await app.close()
    asyncio.run(main())


def test_comparisons_follow_the_charts_timeframe_with_their_own_series_id(tmp_path):
    async def main():
        app = ChartApp(RecordingMarket(), tmp_path / "data", tmp_path / "indicators")
        chart = await app.create_chart("w1", ChartCreate(instrumentId=BTC["instrumentId"], timeframe="1m"))
        await app.commit("w1", commit(chart["chartId"], 1, {"type": "comparison.add", "id": "c1",
                                                            "instrumentId": ETH["instrumentId"]}))
        state = await app.commit("w1", commit(chart["chartId"], 2, {"type": "selection.set", "timeframe": "1h"}))
        assert state["document"]["comparisons"][0]["series"] == series_of(ETH, "1h")
        await app.close()
    asyncio.run(main())


def call(app, name, value):
    return actions.handlers(app)[name](value, USER)
